from sqlalchemy import create_engine, Column, String, Integer, DateTime, Text, Enum, BigInteger
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from datetime import datetime, timedelta, timezone
import enum
from app.config import settings

DATABASE_URL = settings.database_url

engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    echo=False,
    future=True
)

SessionLocal = sessionmaker(
    autocommit=False, 
    autoflush=False, 
    bind=engine
)

Base = declarative_base()


class ProcessingStatus(str, enum.Enum):
    """Request processing states"""
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class IdempotencyRecord(Base):
    """Idempotency key source of truth"""
    __tablename__ = "idempotency_records"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    idempotency_key = Column(String(255), unique=True, nullable=False, index=True)
    
    request_hash = Column(String(64), nullable=False)
    
    request_body = Column(Text, nullable=True)
    response_body = Column(Text, nullable=True)
    response_status_code = Column(Integer, nullable=True)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False, index=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    expires_at = Column(DateTime, nullable=False, index=True)
    status = Column(Enum(ProcessingStatus), nullable=False, default=ProcessingStatus.PROCESSING, index=True)
    retry_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    
    def __repr__(self):
        return f"<IdempotencyRecord(key={self.idempotency_key[:8]}..., status={self.status})>"


class PaymentTransaction(Base):
    """Successful payment audit trail"""
    __tablename__ = "payment_transactions"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    
    transaction_id = Column(String(255), unique=True, nullable=False, index=True)
    
    idempotency_key = Column(String(255), nullable=False, index=True)
    amount = Column(Integer, nullable=False)  # Stored in CENTS (e.g., 100 = $1.00)
    currency = Column(String(3), nullable=False, default="USD")
    status = Column(String(50), nullable=False, index=True)
    
    provider_response = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)
    completed_at = Column(DateTime, nullable=True)
    
    def __repr__(self):
        return f"<PaymentTransaction(id={self.transaction_id}, amount={self.amount}, status={self.status})>"


def init_db():
    try:
        Base.metadata.create_all(bind=engine)
        print(" Database tables created successfully")
    except Exception as e:
        print(f" Failed to create database tables: {e}")
        raise


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

