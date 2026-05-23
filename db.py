from sqlalchemy import create_engine, Column, Integer, String, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# Cria o engine do SQLite (arquivo local chamado 'bot.db')
engine = create_engine('sqlite:///bot.db', connect_args={"check_same_thread": False})

# Base para os modelos
Base = declarative_base()

# Sessão para interagir com o banco
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Modelo para mensagens de notificação
class NotificationMessage(Base):
    __tablename__ = 'notification_messages'

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String(50), index=True)  # Ex: 'pre_notification', 'unreal_news'
    message = Column(Text, nullable=False)

# Modelo para configurações chave-valor
class Config(Base):
    __tablename__ = 'configs'

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, index=True, nullable=False)
    value = Column(String(200), nullable=False)

# Função para criar as tabelas no banco de dados
def init_db():
    Base.metadata.create_all(bind=engine)

# Função para obter todas as notificações
def get_all_notifications(db: Session):
    return db.query(NotificationMessage).all()

# Função para obter notificações por categoria
def get_notifications_by_category(db: Session, category: str):
    return db.query(NotificationMessage).filter(NotificationMessage.category == category).all()
