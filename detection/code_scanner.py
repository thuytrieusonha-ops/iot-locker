#!/usr/bin/env python3
"""Realtime QR and barcode scanner using OpenCV webcam."""

from __future__ import annotations

import argparse
import time

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


def normalize_points(points) -> list[tuple[int, int]]:
    if points is None:
        return []

    normalized = []
    for point in points:
        x, y = point
        normalized.append((int(x), int(y)))
    return normalized


def map_points_to_original(
    points,
    scale_x: float,
    scale_y: float,
    offset_x: int,
    offset_y: int,
) -> list[tuple[int, int]]:
    normalized = []
    if points is None:
        return normalized

    for point in points:
        x, y = point
        mapped_x = int((x / scale_x) + offset_x)
        mapped_y = int((y / scale_y) + offset_y)
        normalized.append((mapped_x, mapped_y))
    return normalized


def build_scan_variants(frame, crop_ratio: float, upscale_factor: float) -> list[dict]:
    frame_h, frame_w = frame.shape[:2]
    variants = [
        {
            "name": "original",
            "image": frame,
            "scale_x": 1.0,
            "scale_y": 1.0,
            "offset_x": 0,
            "offset_y": 0,
        }
    ]

    enlarged = cv2.resize(frame, None, fx=upscale_factor, fy=upscale_factor, interpolation=cv2.INTER_CUBIC)
    variants.append(
        {
            "name": "upscaled",
            "image": enlarged,
            "scale_x": upscale_factor,
            "scale_y": upscale_factor,
            "offset_x": 0,
            "offset_y": 0,
        }
    )

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    sharpened = cv2.GaussianBlur(enhanced, (0, 0), 3)
    enhanced = cv2.addWeighted(enhanced, 1.7, sharpened, -0.7, 0)
    enhanced_upscaled = cv2.resize(
        enhanced,
        None,
        fx=upscale_factor,
        fy=upscale_factor,
        interpolation=cv2.INTER_CUBIC,
    )
    variants.append(
        {
            "name": "enhanced",
            "image": enhanced_upscaled,
            "scale_x": upscale_factor,
            "scale_y": upscale_factor,
            "offset_x": 0,
            "offset_y": 0,
        }
    )

    crop_w = int(frame_w * crop_ratio)
    crop_h = int(frame_h * crop_ratio)
    crop_x = (frame_w - crop_w) // 2
    crop_y = (frame_h - crop_h) // 2
    cropped = frame[crop_y : crop_y + crop_h, crop_x : crop_x + crop_w]
    cropped_upscaled = cv2.resize(
        cropped,
        None,
        fx=upscale_factor,
        fy=upscale_factor,
        interpolation=cv2.INTER_CUBIC,
    )
    variants.append(
        {
            "name": "center-crop",
            "image": cropped_upscaled,
            "scale_x": upscale_factor,
            "scale_y": upscale_factor,
            "offset_x": crop_x,
            "offset_y": crop_y,
        }
    )

    return variants


def draw_polygon(frame, points: list[tuple[int, int]], color: tuple[int, int, int]) -> None:
    if len(points) < 2:
        return

    for index, point in enumerate(points):
        next_point = points[(index + 1) % len(points)]
        cv2.line(frame, point, next_point, color, 2)
        cv2.circle(frame, point, 4, color, -1)


def draw_label(frame, text: str, origin: tuple[int, int], color: tuple[int, int, int]) -> None:
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


def scan_qr_codes(qr_detector, variants: list[dict]) -> list[dict]:
    results: list[dict] = []
    for variant in variants:
        found, decoded_info, points, _ = qr_detector.detectAndDecodeMulti(variant["image"])
        if found and points is not None:
            for text, polygon in zip(decoded_info, points):
                text = text.strip()
                if not text:
                    continue
                results.append(
                    {
                        "kind": "QR",
                        "value": text,
                        "points": map_points_to_original(
                            polygon,
                            variant["scale_x"],
                            variant["scale_y"],
                            variant["offset_x"],
                            variant["offset_y"],
                        ),
                    }
                )

        text, points, _ = qr_detector.detectAndDecode(variant["image"])
        text = text.strip()
        if text and points is not None:
            results.append(
                {
                    "kind": "QR",
                    "value": text,
                    "points": map_points_to_original(
                        points,
                        variant["scale_x"],
                        variant["scale_y"],
                        variant["offset_x"],
                        variant["offset_y"],
                    ),
                }
            )
    return results


def scan_barcodes(barcode_detector, variants: list[dict]) -> list[dict]:
    results: list[dict] = []
    for variant in variants:
        found, decoded_info, decoded_types, points = barcode_detector.detectAndDecodeWithType(
            variant["image"]
        )
        if found and points is not None:
            for text, barcode_type, polygon in zip(decoded_info, decoded_types, points):
                text = text.strip()
                if not text:
                    continue
                results.append(
                    {
                        "kind": barcode_type or "BARCODE",
                        "value": text,
                        "points": map_points_to_original(
                            polygon,
                            variant["scale_x"],
                            variant["scale_y"],
                            variant["offset_x"],
                            variant["offset_y"],
                        ),
                    }
                )
    return results


def deduplicate_results(results: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for result in results:
        key = (result["kind"], result["value"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(result)
    return deduped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Realtime QR/barcode scanner with webcam."
    )
    parser.add_argument("--source", default="0")
    parser.add_argument("--select-webcam", action="store_true")
    parser.add_argument("--max-webcam-index", type=int, default=10)
    parser.add_argument("--frame-width", type=int, default=1280)
    parser.add_argument("--frame-height", type=int, default=720)
    parser.add_argument("--target-fps", type=int, default=30)
    parser.add_argument("--buffer-size", type=int, default=1)
    parser.add_argument("--skip-frames", type=int, default=1)
    parser.add_argument("--flip", action="store_true")
    parser.add_argument("--repeat-delay", type=float, default=2.0)
    parser.add_argument("--crop-ratio", type=float, default=0.7)
    parser.add_argument("--upscale-factor", type=float, default=2.0)
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

    qr_detector = cv2.QRCodeDetector()
    barcode_detector = cv2.barcode_BarcodeDetector()
    barcode_detector.setDetectorScales([0.01, 0.03, 0.06, 0.08])

    last_reported_at: dict[tuple[str, str], float] = {}
    previous_time = time.perf_counter()

    try:
        while True:
            ok, frame = read_latest_frame(cap, args.skip_frames)
            if not ok:
                print("Khong doc duoc frame tu camera.")
                break

            if args.flip:
                frame = cv2.flip(frame, 1)

            variants = build_scan_variants(frame, args.crop_ratio, args.upscale_factor)
            qr_results = scan_qr_codes(qr_detector, variants)
            barcode_results = scan_barcodes(barcode_detector, variants)
            results = deduplicate_results(qr_results + barcode_results)

            current_time = time.perf_counter()
            fps = 1.0 / max(current_time - previous_time, 1e-6)
            previous_time = current_time

            for result in results:
                points = result["points"]
                color = (0, 255, 0) if result["kind"] == "QR" else (0, 165, 255)
                draw_polygon(frame, points, color)

                if points:
                    min_x = min(point[0] for point in points)
                    min_y = min(point[1] for point in points)
                    draw_label(
                        frame,
                        f'{result["kind"]}: {result["value"]}',
                        (min_x, max(25, min_y - 8)),
                        color,
                    )

                key = (result["kind"], result["value"])
                last_time = last_reported_at.get(key, 0.0)
                if current_time - last_time >= args.repeat_delay:
                    print(f'[{result["kind"]}] {result["value"]}')
                    last_reported_at[key] = current_time

            draw_label(frame, f"FPS: {fps:.1f}", (15, 28), (255, 255, 255))
            draw_label(frame, f"Codes: {len(results)}", (15, 58), (255, 255, 255))
            draw_label(frame, "dua ma gan hon | q: quit", (15, 88), (255, 255, 255))

            cv2.imshow("QR and Barcode Scanner", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
