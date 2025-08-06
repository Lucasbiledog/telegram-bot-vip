from sqlalchemy import create_engine, Column, Integer, String, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

Base = declarative_base()

class NotificationMessage(Base):
    __tablename__ = 'notification_messages'
    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String, index=True)  # 'pre_notification' ou 'unreal_news'
    message = Column(Text)

class Config(Base):
    __tablename__ = 'config'
    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String, unique=True)
    value = Column(String)

# Cria conexão com SQLite (arquivo local)
engine = create_engine('sqlite:///bot_data.db', connect_args={"check_same_thread": False})

# Cria sessão para operar banco
SessionLocal = sessionmaker(bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)
