from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from .models import Base, NotificationMessage

engine = create_engine('sqlite:///bot.db', connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db() -> None:
    Base.metadata.create_all(bind=engine)

def get_all_notifications(db: Session):
    return db.query(NotificationMessage).all()


def get_notifications_by_category(db: Session, category: str):
    return db.query(NotificationMessage).filter(NotificationMessage.category == category).all()
