from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import build_database_url


class Base(DeclarativeBase):
    pass


def load_dotenv_file(filename: str = ".env") -> None:
    dotenv_path = Path(__file__).resolve().parent / filename
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue

        cleaned = value.strip()
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
            cleaned = cleaned[1:-1]
        os.environ[key] = cleaned


load_dotenv_file()

DATABASE_URL = build_database_url()

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    class_=Session,
    expire_on_commit=False,
) if engine is not None else None


def is_database_configured() -> bool:
    return engine is not None and SessionLocal is not None


def init_db() -> None:
    if not is_database_configured():
        return

    from model import Base as ModelBase

    ModelBase.metadata.create_all(bind=engine)
    ensure_schema_updates()


def ensure_schema_updates() -> None:
    if engine is None:
        return

    inspector = inspect(engine)
    if "locker_orders" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("locker_orders")}
    statements: list[str] = []

    if "recipient_email" not in existing_columns:
        statements.append("ALTER TABLE locker_orders ADD COLUMN recipient_email VARCHAR(255) NULL")
    if "email_delivery_status" not in existing_columns:
        statements.append("ALTER TABLE locker_orders ADD COLUMN email_delivery_status VARCHAR(20) NULL")
    if "email_delivery_note" not in existing_columns:
        statements.append("ALTER TABLE locker_orders ADD COLUMN email_delivery_note VARCHAR(255) NULL")
    if "email_sent_at" not in existing_columns:
        statements.append("ALTER TABLE locker_orders ADD COLUMN email_sent_at DATETIME NULL")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def get_db() -> Generator[Session, None, None]:
    if SessionLocal is None:
        raise RuntimeError("SMARTLOCKER_DATABASE_URL is not configured.")

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
