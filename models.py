from __future__ import annotations
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import declarative_base, Mapped, mapped_column
from sqlalchemy import BigInteger, Integer, String, Boolean, DateTime, Text, Float

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64))
    is_vip: Mapped[bool] = mapped_column(Boolean, default=False)
    vip_until: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Admin(Base):
    __tablename__ = "admins"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)

class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, index=True)
    tx_hash: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    chain: Mapped[Optional[str]] = mapped_column(String(24))
    token_contract: Mapped[Optional[str]] = mapped_column(String(64))
    amount_raw: Mapped[Optional[int]] = mapped_column(BigInteger)
    amount_usd: Mapped[Optional[float]] = mapped_column(Float)
    confirmations: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|approved|rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

class Config(Base):
    __tablename__ = "configs"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
