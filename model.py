from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class UserAccount(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)

    orders: Mapped[list[LockerOrder]] = relationship(back_populates="user")


class LockerSite(Base):
    __tablename__ = "locker_sites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)

    lockers: Mapped[list[Locker]] = relationship(back_populates="site")


class Locker(Base):
    __tablename__ = "lockers"
    __table_args__ = (
        UniqueConstraint("site_id", "locker_number", name="uq_lockers_site_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id: Mapped[int] = mapped_column(
        ForeignKey("locker_sites.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
        index=True,
    )
    locker_number: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)

    site: Mapped[LockerSite] = relationship(back_populates="lockers")
    orders: Mapped[list[LockerOrder]] = relationship(back_populates="locker")
    admin_command_links: Mapped[list[AdminCommandLocker]] = relationship(back_populates="locker")


class LockerOrder(Base):
    __tablename__ = "locker_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
        index=True,
    )
    locker_id: Mapped[int] = mapped_column(
        ForeignKey("lockers.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
        index=True,
    )
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

    user: Mapped[UserAccount | None] = relationship(back_populates="orders")
    locker: Mapped[Locker] = relationship(back_populates="orders")
    access_tokens: Mapped[list[LockerAccessToken]] = relationship(back_populates="order")


class LockerAccessToken(Base):
    __tablename__ = "locker_access_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("locker_orders.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
        index=True,
    )
    locker_id: Mapped[int] = mapped_column(
        ForeignKey("lockers.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
        index=True,
    )
    phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    delivery_channel: Mapped[str] = mapped_column(String(20), nullable=False, default="email")
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    order: Mapped[LockerOrder] = relationship(back_populates="access_tokens")


class AdminCommand(Base):
    __tablename__ = "admin_commands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    locker_links: Mapped[list[AdminCommandLocker]] = relationship(
        back_populates="command",
        cascade="all, delete-orphan",
    )


class AdminCommandLocker(Base):
    __tablename__ = "admin_command_lockers"

    command_id: Mapped[int] = mapped_column(
        ForeignKey("admin_commands.id", ondelete="CASCADE", onupdate="CASCADE"),
        primary_key=True,
    )
    locker_id: Mapped[int] = mapped_column(
        ForeignKey("lockers.id", ondelete="RESTRICT", onupdate="CASCADE"),
        primary_key=True,
    )

    command: Mapped[AdminCommand] = relationship(back_populates="locker_links")
    locker: Mapped[Locker] = relationship(back_populates="admin_command_links")
