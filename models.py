from __future__ import annotations
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer, Boolean, DateTime, Text
from datetime import datetime
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
    vip_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
