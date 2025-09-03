from __future__ import annotations
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer, Boolean, DateTime, Text
from datetime import datetime, timezone

from typing import Optional

class Base(DeclarativeBase):
    pass

class Config(Base):
    __tablename__ = "config"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text)

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    is_vip: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    requeued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    vip_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tx_hash: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    tg_id: Mapped[int] = mapped_column(Integer, nullable=False)
    validated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

class Pack(Base):
    __tablename__ = "packs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    previews: Mapped[str] = mapped_column(Text, default="[]")
    files: Mapped[str] = mapped_column(Text, default="[]")
    is_vip: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)