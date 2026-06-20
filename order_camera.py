from __future__ import annotations

import base64
import binascii
import os
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4


MAX_ORDER_PHOTO_BYTES = 5 * 1024 * 1024
JPEG_DATA_URL_PREFIX = "data:image/jpeg;base64,"


class OrderPhotoError(ValueError):
    pass


def _safe_order_code(order_code: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", order_code.strip()).strip("-")
    return cleaned[:64] or "order"


def _photo_root() -> Path:
    return Path(os.getenv("SMARTLOCKER_ORDER_PHOTO_DIR", "order_photos"))


def save_order_photo(
    data_url: str,
    order_code: str,
    locker_id: int,
    pickup_code: str = "",
) -> Path | None:
    """Decode and persist a JPEG captured by the kiosk camera."""
    payload = data_url.strip()
    if not payload:
        return None
    if not payload.startswith(JPEG_DATA_URL_PREFIX):
        raise OrderPhotoError("Ảnh chụp không đúng định dạng JPEG.")

    try:
        photo_bytes = base64.b64decode(payload[len(JPEG_DATA_URL_PREFIX) :], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise OrderPhotoError("Dữ liệu ảnh chụp không hợp lệ.") from exc

    if not photo_bytes.startswith(b"\xff\xd8\xff"):
        raise OrderPhotoError("Dữ liệu nhận được không phải ảnh JPEG.")
    if len(photo_bytes) > MAX_ORDER_PHOTO_BYTES:
        raise OrderPhotoError("Ảnh chụp vượt quá giới hạn 5 MB.")

    root = _photo_root()
    day_directory = root / datetime.now().strftime("%Y-%m-%d")
    day_directory.mkdir(parents=True, exist_ok=True)
    filename = (
        f"{datetime.now():%H%M%S}_locker-{locker_id}_"
        f"{_safe_order_code(order_code)}_pickup-{_safe_order_code(pickup_code)}_"
        f"{uuid4().hex[:8]}.jpg"
    )
    photo_path = day_directory / filename
    photo_path.write_bytes(photo_bytes)
    return photo_path


def find_order_photo(pickup_code: str) -> Path | None:
    """Find the newest stored photo belonging to a pickup code."""
    cleaned_pickup_code = _safe_order_code(pickup_code)
    candidates = list(_photo_root().glob(f"*/*_pickup-{cleaned_pickup_code}_*.jpg"))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)
