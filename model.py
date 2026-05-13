from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class LockerOrder(Base):
    __tablename__ = "locker_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    locker_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    pickup_code: Mapped[str] = mapped_column(String(12), nullable=False, unique=True, index=True)
    flow: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    order_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    recipient_email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    email_delivery_status: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    email_delivery_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_link_base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="stored", index=True)


class UserAccount(Base):
    __tablename__ = "user_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)


class LockerAccessToken(Base):
    __tablename__ = "locker_access_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    locker_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    delivery_channel: Mapped[str] = mapped_column(String(20), nullable=False, default="email")
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AdminCommand(Base):
    __tablename__ = "admin_commands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
