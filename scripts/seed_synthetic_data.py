from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import SessionLocal, init_db, is_database_configured
from model import LockerEvent, LockerOrder, ParcelImageAsset, ParcelInferenceResult


SIM_PREFIX = "SIM"
EVENT_TYPES = (
    "order_stored",
    "door_open",
    "photo_captured",
    "order_collected",
    "issue_report",
)
LABELS = ("parcel", "oversized", "damaged", "unknown")
MODEL_NAMES = ("yolov8n", "yolov8s", "parcel-tracker-v1")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed synthetic SmartLocker analytics data.")
    parser.add_argument("--sites", type=int, default=4, help="Number of locker sites to simulate.")
    parser.add_argument("--lockers-per-site", type=int, default=10, help="Number of lockers per site.")
    parser.add_argument("--days", type=int, default=30, help="How many past days to generate.")
    parser.add_argument("--orders-per-site-per-day", type=int, default=20, help="Daily orders per site.")
    parser.add_argument("--wipe", action="store_true", help="Delete existing synthetic rows before seeding.")
    parser.add_argument("--seed", type=int, default=20260621, help="Random seed for repeatable runs.")
    return parser.parse_args()


def wipe_synthetic_rows() -> None:
    assert SessionLocal is not None
    with SessionLocal() as session:
        session.execute(delete(ParcelInferenceResult).where(ParcelInferenceResult.pickup_code.like(f"{SIM_PREFIX}-%")))
        session.execute(delete(ParcelImageAsset).where(ParcelImageAsset.pickup_code.like(f"{SIM_PREFIX}-%")))
        session.execute(delete(LockerEvent).where(LockerEvent.pickup_code.like(f"{SIM_PREFIX}-%")))
        session.execute(delete(LockerOrder).where(LockerOrder.order_code.like(f"{SIM_PREFIX}-%")))
        session.commit()


def build_pickup_code(site_id: int, day_index: int, order_index: int) -> str:
    return f"{SIM_PREFIX}{site_id:02d}{day_index:03d}{order_index:03d}{uuid4().hex[:4].upper()}"[:12]


def build_phone(site_id: int, order_index: int, day_index: int) -> str:
    suffix = f"{site_id:02d}{day_index:03d}{order_index:03d}"[-8:]
    return f"09{suffix}"


def seed_synthetic_rows(args: argparse.Namespace) -> dict[str, int]:
    random.seed(args.seed)
    assert SessionLocal is not None

    order_count = 0
    event_count = 0
    image_count = 0
    inference_count = 0

    with SessionLocal() as session:
        now = datetime.now()
        for day_index in range(args.days):
            day_base = (now - timedelta(days=day_index)).replace(hour=8, minute=0, second=0, microsecond=0)
            for site_id in range(1, args.sites + 1):
                for order_index in range(args.orders_per_site_per_day):
                    locker_id = ((order_index % args.lockers_per_site) + 1)
                    created_at = day_base + timedelta(minutes=order_index * 12 + site_id)
                    pickup_code = build_pickup_code(site_id, day_index, order_index)
                    order_code = f"{SIM_PREFIX}-S{site_id:02d}-D{day_index:03d}-O{order_index:03d}"
                    collected = random.random() < 0.87
                    order = LockerOrder(
                        locker_id=locker_id,
                        phone=build_phone(site_id, order_index, day_index),
                        pickup_code=pickup_code,
                        flow="shipper_dropoff" if order_index % 3 else "user_dropoff",
                        created_at=created_at,
                        order_code=order_code,
                        recipient_email=f"user{site_id}_{day_index}_{order_index}@example.com",
                        email_delivery_status="sent" if collected else random.choice(["sent", "pending", "failed"]),
                        email_delivery_note="synthetic workload",
                        status="collected" if collected else "stored",
                    )
                    session.add(order)
                    session.flush()
                    order_count += 1

                    events = [
                        LockerEvent(
                            site_id=site_id,
                            order_id=order.id,
                            locker_id=locker_id,
                            phone=order.phone,
                            pickup_code=pickup_code,
                            event_type="order_stored",
                            status="ok",
                            payload='{"source":"synthetic","step":"store"}',
                            created_at=created_at,
                        ),
                        LockerEvent(
                            site_id=site_id,
                            order_id=order.id,
                            locker_id=locker_id,
                            phone=order.phone,
                            pickup_code=pickup_code,
                            event_type="door_open",
                            status="ok",
                            payload='{"source":"synthetic","step":"dropoff-open"}',
                            created_at=created_at + timedelta(seconds=15),
                        ),
                        LockerEvent(
                            site_id=site_id,
                            order_id=order.id,
                            locker_id=locker_id,
                            phone=order.phone,
                            pickup_code=pickup_code,
                            event_type="photo_captured",
                            status="ok",
                            payload='{"source":"synthetic","camera":"kiosk-top"}',
                            created_at=created_at + timedelta(seconds=20),
                        ),
                    ]
                    if collected:
                        pickup_time = created_at + timedelta(hours=random.randint(1, 48))
                        events.extend(
                            [
                                LockerEvent(
                                    site_id=site_id,
                                    order_id=order.id,
                                    locker_id=locker_id,
                                    phone=order.phone,
                                    pickup_code=pickup_code,
                                    event_type="door_open",
                                    status="ok",
                                    payload='{"source":"synthetic","step":"pickup-open"}',
                                    created_at=pickup_time,
                                ),
                                LockerEvent(
                                    site_id=site_id,
                                    order_id=order.id,
                                    locker_id=locker_id,
                                    phone=order.phone,
                                    pickup_code=pickup_code,
                                    event_type="order_collected",
                                    status="ok",
                                    payload='{"source":"synthetic","step":"pickup-complete"}',
                                    created_at=pickup_time + timedelta(seconds=40),
                                ),
                            ]
                        )
                    elif random.random() < 0.10:
                        events.append(
                            LockerEvent(
                                site_id=site_id,
                                order_id=order.id,
                                locker_id=locker_id,
                                phone=order.phone,
                                pickup_code=pickup_code,
                                event_type="issue_report",
                                status="warning",
                                payload='{"source":"synthetic","issue":"cannot_receive_email"}',
                                created_at=created_at + timedelta(hours=4),
                            )
                        )

                    session.add_all(events)
                    event_count += len(events)

                    image = ParcelImageAsset(
                        site_id=site_id,
                        order_id=order.id,
                        locker_id=locker_id,
                        pickup_code=pickup_code,
                        storage_path=f"synthetic/site-{site_id:02d}/{created_at:%Y-%m-%d}/{pickup_code}.jpg",
                        file_size_bytes=random.randint(180_000, 950_000),
                        captured_at=created_at + timedelta(seconds=20),
                    )
                    session.add(image)
                    session.flush()
                    image_count += 1

                    for _ in range(random.randint(1, 3)):
                        session.add(
                            ParcelInferenceResult(
                                site_id=site_id,
                                order_id=order.id,
                                image_id=image.id,
                                locker_id=locker_id,
                                pickup_code=pickup_code,
                                model_name=random.choice(MODEL_NAMES),
                                label=random.choice(LABELS),
                                confidence=round(random.uniform(0.72, 0.995), 4),
                                tracked=random.choice((0, 1)),
                                inference_ms=random.randint(24, 180),
                                created_at=created_at + timedelta(seconds=22),
                            )
                        )
                        inference_count += 1

            session.commit()

    return {
        "orders": order_count,
        "events": event_count,
        "images": image_count,
        "inferences": inference_count,
    }


def main() -> None:
    args = parse_args()
    if not is_database_configured() or SessionLocal is None:
        raise RuntimeError("SMARTLOCKER_DATABASE_URL is not configured.")

    init_db()
    if args.wipe:
        wipe_synthetic_rows()

    counts = seed_synthetic_rows(args)
    print("Synthetic data generation complete:")
    for key, value in counts.items():
        print(f"  - {key}: {value}")


if __name__ == "__main__":
    main()
