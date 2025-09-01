from sqlalchemy import Column, Integer, String, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class NotificationMessage(Base):
    __tablename__ = "notification_messages"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String(50), index=True)
    message = Column(Text, nullable=False)


class Config(Base):
    __tablename__ = "configs"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, index=True, nullable=False)
    value = Column(String(200), nullable=False)
