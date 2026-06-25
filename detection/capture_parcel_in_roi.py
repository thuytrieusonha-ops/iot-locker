#!/usr/bin/env python3
"""Demo camera with capture frame guide for parcel + shipping label photos."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2


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


def compute_capture_roi(
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


def draw_capture_overlay(frame, roi: tuple[int, int, int, int]) -> None:
    x1, y1, x2, y2 = roi
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (frame.shape[1], frame.shape[0]), (0, 0, 0), -1)
    cv2.rectangle(overlay, (x1, y1), (x2, y2), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.25, frame, 0.75, 0, frame)

    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 255), 3)
    guide_w = max(40, (x2 - x1) // 6)
    guide_h = max(40, (y2 - y1) // 6)

    corners = [
        (x1, y1, x1 + guide_w, y1 + guide_h),
        (x2, y1, x2 - guide_w, y1 + guide_h),
        (x1, y2, x1 + guide_w, y2 - guide_h),
        (x2, y2, x2 - guide_w, y2 - guide_h),
    ]
    for cx, cy, gx, gy in corners:
        cv2.line(frame, (cx, cy), (gx, cy), (0, 255, 255), 4)
        cv2.line(frame, (cx, cy), (cx, gy), (0, 255, 255), 4)


def compute_capture_button(frame_shape) -> tuple[int, int, int, int]:
    frame_h, frame_w = frame_shape[:2]
    button_w = 220
    button_h = 70
    margin = 30
    x1 = frame_w - button_w - margin
    y1 = frame_h - button_h - margin
    return x1, y1, x1 + button_w, y1 + button_h


def draw_capture_button(frame, button_rect: tuple[int, int, int, int], active: bool) -> None:
    x1, y1, x2, y2 = button_rect
    color = (0, 180, 0) if not active else (0, 120, 255)
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, -1)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 255, 255), 2)
    label = "BAT DAU CHUP" if not active else "DANG DEM 5S"
    draw_text_with_bg(frame, label, (x1 + 18, y1 + 42), (255, 255, 255))


def point_in_rect(point: tuple[int, int], rect: tuple[int, int, int, int]) -> bool:
    x, y = point
    x1, y1, x2, y2 = rect
    return x1 <= x <= x2 and y1 <= y <= y2


def save_capture(frame, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = output_dir / f"parcel_demo_{timestamp}.jpg"
    cv2.imwrite(str(filename), frame)
    return filename


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Demo capture screen for parcel + shipping label photo."
    )
    parser.add_argument("--source", default="0")
    parser.add_argument("--select-webcam", action="store_true")
    parser.add_argument("--max-webcam-index", type=int, default=10)
    parser.add_argument("--select-roi", action="store_true")
    parser.add_argument("--roi-width", type=float, default=0.55)
    parser.add_argument("--roi-height", type=float, default=0.55)
    parser.add_argument("--frame-width", type=int, default=1280)
    parser.add_argument("--frame-height", type=int, default=720)
    parser.add_argument("--target-fps", type=int, default=30)
    parser.add_argument("--buffer-size", type=int, default=1)
    parser.add_argument("--skip-frames", type=int, default=1)
    parser.add_argument("--flip", action="store_true")
    parser.add_argument("--output-dir", default="captures")
    parser.add_argument("--countdown-seconds", type=float, default=5.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()

    source = (
        choose_webcam_interactively(args.max_webcam_index)
        if args.select_webcam
        else parse_source(args.source)
    )

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
    roi = compute_capture_roi(first_frame.shape, args.roi_width, args.roi_height)
    if args.select_roi:
        selected = cv2.selectROI("Select Capture ROI", first_frame, fromCenter=False, showCrosshair=True)
        cv2.destroyWindow("Select Capture ROI")
        sx, sy, sw, sh = selected
        if sw > 0 and sh > 0:
            roi = clip_roi((sx, sy, sx + sw, sy + sh), frame_w, frame_h)

    output_dir = Path(args.output_dir)
    latest_saved_path = ""
    previous_time = time.perf_counter()
    countdown_started_at: float | None = None
    last_frame = first_frame.copy()
    state = {
        "button_clicked": False,
    }

    def on_mouse(event, x, y, flags, param) -> None:
        del flags, param
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        button_rect = compute_capture_button(last_frame.shape)
        if point_in_rect((x, y), button_rect):
            state["button_clicked"] = True

    cv2.namedWindow("Parcel Capture Demo")
    cv2.setMouseCallback("Parcel Capture Demo", on_mouse)

    try:
        while True:
            ok, frame = read_latest_frame(cap, args.skip_frames)
            if not ok:
                print("Khong doc duoc frame tu camera.")
                break

            if args.flip:
                frame = cv2.flip(frame, 1)

            preview = frame.copy()
            last_frame = preview
            draw_capture_overlay(preview, roi)
            button_rect = compute_capture_button(preview.shape)

            now = time.perf_counter()
            fps = 1.0 / max(now - previous_time, 1e-6)
            previous_time = now

            draw_text_with_bg(preview, f"FPS: {fps:.1f}", (15, 28))
            draw_text_with_bg(preview, "Dat kien hang + tem van don vao trong khung", (15, 58), (0, 255, 255))
            draw_text_with_bg(preview, "Bam nut tren man hinh hoac SPACE de dem 5s", (15, 88))
            draw_text_with_bg(preview, "q: thoat", (15, 118))
            if latest_saved_path:
                draw_text_with_bg(preview, f"Saved: {Path(latest_saved_path).name}", (15, 148), (0, 255, 0))

            if state["button_clicked"] and countdown_started_at is None:
                countdown_started_at = now
                state["button_clicked"] = False

            if countdown_started_at is not None:
                remaining = max(0.0, args.countdown_seconds - (now - countdown_started_at))
                draw_capture_button(preview, button_rect, active=True)
                draw_text_with_bg(
                    preview,
                    f"Tu dong chup sau: {remaining:.1f}s",
                    (15, 178),
                    (0, 200, 255),
                )
                if remaining <= 0.0:
                    saved_path = save_capture(frame, output_dir)
                    latest_saved_path = str(saved_path)
                    countdown_started_at = None
                    print(f"Da luu anh: {saved_path}")
            else:
                draw_capture_button(preview, button_rect, active=False)

            cv2.imshow("Parcel Capture Demo", preview)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == 32:
                countdown_started_at = now
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
