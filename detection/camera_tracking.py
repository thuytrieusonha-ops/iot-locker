#!/usr/bin/env python3
"""Detect and lock parcels from webcam with YOLOv8n NCNN."""

from __future__ import annotations

import argparse
import math
import time
from pathlib import Path

import cv2

try:
    from ultralytics import YOLO
except ImportError as exc:  # pragma: no cover - runtime dependency
    raise SystemExit("Chua cai ultralytics. Hay chay `uv sync`.") from exc


def parse_source(source: str) -> int | str:
    return int(source) if source.isdigit() else source


def list_available_webcams(max_index: int = 10) -> list[int]:
    available_indexes: list[int] = []
    for index in range(max_index):
        cap = cv2.VideoCapture(index)
        if cap.isOpened():
            ok, _ = cap.read()
            if ok:
                available_indexes.append(index)
        cap.release()
    return available_indexes


def choose_webcam_interactively(max_index: int = 10) -> int:
    webcams = list_available_webcams(max_index=max_index)
    if not webcams:
        raise SystemExit("Khong tim thay webcam nao kha dung.")

    print("Danh sach webcam kha dung:")
    for webcam_index in webcams:
        print(f"  [{webcam_index}] Webcam {webcam_index}")

    while True:
        selected = input(f"Chon webcam {webcams}: ").strip()
        if selected.isdigit() and int(selected) in webcams:
            return int(selected)
        print("Lua chon khong hop le, vui long nhap lai.")


def open_video_capture(
    source: int | str,
    width: int,
    height: int,
    target_fps: int,
    buffer_size: int,
):
    cap = cv2.VideoCapture(source)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, buffer_size)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, target_fps)
    return cap


def read_latest_frame(cap, skip_frames: int):
    for _ in range(skip_frames):
        if not cap.grab():
            break
    return cap.read()


def compute_center_roi(
    frame_shape,
    roi_width_ratio: float,
    roi_height_ratio: float,
) -> tuple[int, int, int, int]:
    frame_h, frame_w = frame_shape[:2]
    roi_w = int(frame_w * roi_width_ratio)
    roi_h = int(frame_h * roi_height_ratio)
    x1 = (frame_w - roi_w) // 2
    y1 = (frame_h - roi_h) // 2
    return x1, y1, x1 + roi_w, y1 + roi_h


def clip_roi(roi: tuple[int, int, int, int], width: int, height: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = roi
    x1 = max(0, min(width - 1, int(x1)))
    y1 = max(0, min(height - 1, int(y1)))
    x2 = max(0, min(width - 1, int(x2)))
    y2 = max(0, min(height - 1, int(y2)))
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    return x1, y1, x2, y2


def bbox_center(bbox: tuple[int, int, int, int]) -> tuple[int, int]:
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) // 2, (y1 + y2) // 2)


def bbox_area(bbox: tuple[int, int, int, int]) -> int:
    x1, y1, x2, y2 = bbox
    return max(0, x2 - x1) * max(0, y2 - y1)


def bbox_distance(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    return math.dist(bbox_center(a), bbox_center(b))


def center_inside_roi(center: tuple[int, int], roi: tuple[int, int, int, int] | None) -> bool:
    if roi is None:
        return True
    cx, cy = center
    x1, y1, x2, y2 = roi
    return x1 <= cx <= x2 and y1 <= cy <= y2


def resolve_model_path(model_path: str, auto_export: bool, imgsz: int) -> str:
    source_path = Path(model_path)
    if source_path.suffix == ".pt":
        if not auto_export:
            raise SystemExit(
                "Model dang la .pt. Hay bat --auto-export hoac truyen duong dan model NCNN da export."
            )
        exported = YOLO(model_path).export(format="ncnn", imgsz=imgsz)
        return str(exported)

    return model_path


def get_model_names(model) -> dict[int, str]:
    names = model.names
    if isinstance(names, dict):
        return {int(key): str(value) for key, value in names.items()}
    return {index: str(name) for index, name in enumerate(names)}


def find_class_ids(names: dict[int, str], labels: list[str]) -> list[int] | None:
    label_set = {label.strip().lower() for label in labels if label.strip()}
    if not label_set:
        return None

    matched_ids = []
    for class_id, class_name in names.items():
        if class_name.strip().lower() in label_set:
            matched_ids.append(class_id)
    return matched_ids or None


def extract_detections(
    result,
    names: dict[int, str],
    target_ids: list[int] | None,
    excluded_ids: list[int] | None,
    min_box_area: int,
) -> list[dict]:
    detections = []
    if result.boxes is None:
        return detections

    for box in result.boxes:
        x1, y1, x2, y2 = box.xyxy[0].detach().cpu().numpy().astype(int).tolist()
        bbox = (x1, y1, x2, y2)
        area = bbox_area(bbox)
        if area < min_box_area:
            continue

        class_id = int(box.cls[0].detach().cpu().item()) if box.cls is not None else -1
        if target_ids is not None and class_id not in target_ids:
            continue
        if excluded_ids is not None and class_id in excluded_ids:
            continue

        detections.append(
            {
                "bbox": bbox,
                "center": bbox_center(bbox),
                "area": area,
                "confidence": float(box.conf[0].detach().cpu().item()) if box.conf is not None else 0.0,
                "class_id": class_id,
                "class_name": names.get(class_id, str(class_id)),
            }
        )
    return detections


def choose_detection_to_lock(detections: list[dict], roi: tuple[int, int, int, int] | None) -> dict | None:
    best_detection = None
    best_score = -1e9
    for detection in detections:
        inside_roi = center_inside_roi(detection["center"], roi)
        if not inside_roi:
            continue
        score = detection["confidence"] * 2.0 + (detection["area"] / 100000.0)
        if score > best_score:
            best_score = score
            best_detection = detection
    return best_detection


def choose_tracking_detection(
    detections: list[dict],
    active_label: str,
    last_bbox: tuple[int, int, int, int] | None,
) -> dict | None:
    if last_bbox is None:
        return None

    best_detection = None
    best_score = -1e9
    for detection in detections:
        same_label_bonus = 0.6 if detection["class_name"] == active_label else 0.0
        distance = bbox_distance(detection["bbox"], last_bbox)
        score = detection["confidence"] * 2.0 + same_label_bonus - (distance / 250.0)
        if score > best_score:
            best_score = score
            best_detection = detection
    return best_detection


def draw_text_with_bg(frame, text: str, origin: tuple[int, int], color=(255, 255, 255)) -> None:
    x, y = origin
    font = cv2.FONT_HERSHEY_SIMPLEX
    (text_w, text_h), baseline = cv2.getTextSize(text, font, 0.6, 2)
    cv2.rectangle(
        frame,
        (x, y - text_h - baseline - 6),
        (x + text_w + 6, y + baseline),
        (0, 0, 0),
        -1,
    )
    cv2.putText(frame, text, (x + 3, y - 4), font, 0.6, color, 2, cv2.LINE_AA)


def draw_roi(frame, roi: tuple[int, int, int, int] | None) -> None:
    if roi is None:
        return
    x1, y1, x2, y2 = roi
    cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 255), 2)
    draw_text_with_bg(frame, "ROI", (x1, max(25, y1 - 8)))


def draw_detections(
    frame,
    detections: list[dict],
    roi: tuple[int, int, int, int] | None,
    active_bbox: tuple[int, int, int, int] | None,
) -> None:
    for detection in detections:
        x1, y1, x2, y2 = detection["bbox"]
        inside_roi = center_inside_roi(detection["center"], roi)
        is_active = active_bbox == detection["bbox"]
        color = (0, 255, 0) if inside_roi else (0, 165, 255)
        if is_active:
            color = (0, 0, 255)

        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.circle(frame, detection["center"], 4, color, -1)
        label = f'{detection["class_name"]} {detection["confidence"]:.2f}'
        if is_active:
            label = f"LOCKED | {label}"
        draw_text_with_bg(frame, label, (x1, max(25, y1 - 8)), color)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Real-time parcel detection with YOLOv8n NCNN."
    )
    parser.add_argument("--model", default="yolov8n.pt")
    parser.add_argument("--auto-export", action="store_true")
    parser.add_argument("--source", default="0")
    parser.add_argument("--select-webcam", action="store_true")
    parser.add_argument("--max-webcam-index", type=int, default=10)
    parser.add_argument("--target-labels", default="")
    parser.add_argument("--exclude-labels", default="person")
    parser.add_argument("--conf", type=float, default=0.35)
    parser.add_argument("--iou", type=float, default=0.45)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--min-box-area", type=int, default=2500)
    parser.add_argument("--hold-frames", type=int, default=5)
    parser.add_argument("--lost-frames", type=int, default=10)
    parser.add_argument("--use-roi", action="store_true")
    parser.add_argument("--select-roi", action="store_true")
    parser.add_argument("--roi-width", type=float, default=0.35)
    parser.add_argument("--roi-height", type=float, default=0.35)
    parser.add_argument("--frame-width", type=int, default=640)
    parser.add_argument("--frame-height", type=int, default=480)
    parser.add_argument("--target-fps", type=int, default=30)
    parser.add_argument("--buffer-size", type=int, default=1)
    parser.add_argument("--skip-frames", type=int, default=1)
    parser.add_argument("--flip", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    source = (
        choose_webcam_interactively(args.max_webcam_index)
        if args.select_webcam
        else parse_source(args.source)
    )

    resolved_model = resolve_model_path(args.model, args.auto_export, args.imgsz)
    model = YOLO(resolved_model)
    names = get_model_names(model)
    target_ids = find_class_ids(names, [item.strip() for item in args.target_labels.split(",")])
    excluded_ids = find_class_ids(names, [item.strip() for item in args.exclude_labels.split(",")])

    cap = open_video_capture(
        source=source,
        width=args.frame_width,
        height=args.frame_height,
        target_fps=args.target_fps,
        buffer_size=args.buffer_size,
    )
    if not cap.isOpened():
        raise SystemExit(f"Khong mo duoc camera/source: {source}")

    ok, first_frame = read_latest_frame(cap, args.skip_frames)
    if not ok:
        raise SystemExit("Khong doc duoc frame dau tien tu webcam.")

    if args.flip:
        first_frame = cv2.flip(first_frame, 1)

    frame_h, frame_w = first_frame.shape[:2]
    roi = compute_center_roi(first_frame.shape, args.roi_width, args.roi_height) if args.use_roi else None
    if args.select_roi:
        selected = cv2.selectROI("Select ROI", first_frame, fromCenter=False, showCrosshair=True)
        cv2.destroyWindow("Select ROI")
        sx, sy, sw, sh = selected
        if sw > 0 and sh > 0:
            roi = clip_roi((sx, sy, sx + sw, sy + sh), frame_w, frame_h)

    pending_bbox: tuple[int, int, int, int] | None = None
    pending_count = 0
    active_bbox: tuple[int, int, int, int] | None = None
    active_label = ""
    lost_count = 0
    previous_time = time.perf_counter()

    try:
        while True:
            ok, frame = read_latest_frame(cap, args.skip_frames)
            if not ok:
                print("Khong doc duoc frame tu camera.")
                break

            if args.flip:
                frame = cv2.flip(frame, 1)

            result = model.predict(
                source=frame,
                conf=args.conf,
                iou=args.iou,
                imgsz=args.imgsz,
                verbose=False,
            )[0]

            detections = extract_detections(
                result,
                names,
                target_ids,
                excluded_ids,
                args.min_box_area,
            )

            if active_bbox is None:
                candidate = choose_detection_to_lock(detections, roi)
                if candidate is None:
                    pending_bbox = None
                    pending_count = 0
                else:
                    if pending_bbox is not None and bbox_distance(candidate["bbox"], pending_bbox) < 80:
                        pending_count += 1
                    else:
                        pending_count = 1
                    pending_bbox = candidate["bbox"]

                    if pending_count >= args.hold_frames:
                        active_bbox = candidate["bbox"]
                        active_label = candidate["class_name"]
                        lost_count = 0
            else:
                tracked = choose_tracking_detection(detections, active_label, active_bbox)
                if tracked is None:
                    lost_count += 1
                    if lost_count > args.lost_frames:
                        active_bbox = None
                        active_label = ""
                        pending_bbox = None
                        pending_count = 0
                else:
                    active_bbox = tracked["bbox"]
                    active_label = tracked["class_name"]
                    lost_count = 0

            output = frame.copy()
            draw_roi(output, roi)
            draw_detections(output, detections, roi, active_bbox)

            current_time = time.perf_counter()
            fps = 1.0 / max(current_time - previous_time, 1e-6)
            previous_time = current_time

            draw_text_with_bg(output, f"Model: {Path(resolved_model).name}", (15, 28))
            draw_text_with_bg(output, f"FPS: {fps:.1f}", (15, 58))
            draw_text_with_bg(output, f"Detected: {len(detections)}", (15, 88))

            if active_bbox is None:
                draw_text_with_bg(output, f"Hold detect: {pending_count}/{args.hold_frames}", (15, 118), (0, 255, 255))
                draw_text_with_bg(output, "Dat parcel vao ROI de khoa muc tieu", (15, 148))
            else:
                x1, y1, x2, y2 = active_bbox
                target_center = bbox_center(active_bbox)
                frame_center = (output.shape[1] // 2, output.shape[0] // 2)
                dx = target_center[0] - frame_center[0]
                dy = target_center[1] - frame_center[1]
                cv2.line(output, frame_center, target_center, (0, 0, 255), 2)
                cv2.circle(output, frame_center, 5, (255, 255, 0), -1)
                cv2.rectangle(output, (x1, y1), (x2, y2), (0, 0, 255), 3)
                draw_text_with_bg(output, f"TRACKING: {active_label}", (15, 118), (0, 0, 255))
                draw_text_with_bg(output, f"dx={dx} dy={dy}", (15, 148), (0, 0, 255))

            cv2.imshow("Parcel YOLOv8n NCNN", output)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("r"):
                pending_bbox = None
                pending_count = 0
                active_bbox = None
                active_label = ""
                lost_count = 0
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
