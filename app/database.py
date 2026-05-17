"""
Database Configuration and Models
This is our audit trail - every request gets recorded here
"""

from sqlalchemy import create_engine, Column, String, Integer, DateTime, Text, Enum, BigInteger
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from datetime import datetime, timedelta
import enum
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database connection URL - add fallback for development
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://admin:password@localhost/idempotency")

# Validate database URL is present
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set")

# Create engine (connection pool)
engine = create_engine(
    DATABASE_URL,
    pool_size=10,           # Max connections in pool
    max_overflow=20,        # Extra connections if pool is full
    pool_pre_ping=True,     # Verify connections before using (good for production)
    echo=False,             # Set to True to see SQL queries
    future=True             # SQLAlchemy 2.0 style
)

# Session factory - creates database sessions
SessionLocal = sessionmaker(
    autocommit=False, 
    autoflush=False, 
    bind=engine
)

# Base class for all models
Base = declarative_base()


class ProcessingStatus(str, enum.Enum):
    """
    Status of an idempotency request
    
    States:
    - PROCESSING: Request is being processed (lock held)
    - COMPLETED: Successfully processed and stored
    - FAILED: Processing failed (client can retry with new key)
    - EXPIRED: Record past retention period (ready for cleanup)
    """
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class IdempotencyRecord(Base):
    """
    Main idempotency table - tracks every unique request
    This is our source of truth for idempotency keys
    
    Indexes:
    - idempotency_key (unique): Fast lookup by key
    - created_at: For cleanup jobs
    - status: For querying by state
    """
    __tablename__ = "idempotency_records"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    
    # The idempotency key provided by the client (UUID v4)
    idempotency_key = Column(String(255), unique=True, nullable=False, index=True)
    
    # SHA-256 hash of the request body - detects tampering
    request_hash = Column(String(64), nullable=False)
    
    # Original request (for audit and debugging)
    request_body = Column(Text, nullable=True)
    
    # Stored response (to return for duplicate requests)
    response_body = Column(Text, nullable=True)
    response_status_code = Column(Integer, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False, index=True)  # For cleanup jobs
    
    # Status tracking
    status = Column(Enum(ProcessingStatus), nullable=False, default=ProcessingStatus.PROCESSING, index=True)
    retry_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    
    def __repr__(self):
        return f"<IdempotencyRecord(key={self.idempotency_key[:8]}..., status={self.status})>"


class PaymentTransaction(Base):
    """
    Permanent record of successful payments
    This is what we'd query for reporting/auditing
    
    Indexes:
    - transaction_id (unique): Fast lookup by transaction
    - idempotency_key: Link back to idempotency record
    - created_at: For time-range queries
    """
    __tablename__ = "payment_transactions"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    
    # Our generated transaction ID (returned to client)
    transaction_id = Column(String(255), unique=True, nullable=False, index=True)
    
    # Reference back to idempotency record
    idempotency_key = Column(String(255), nullable=False, index=True)
    
    # Payment details
    amount = Column(Integer, nullable=False)  # Stored in CENTS (e.g., 100 = $1.00)
    currency = Column(String(3), nullable=False, default="USD")
    status = Column(String(50), nullable=False, index=True)
    
    # Provider response (for debugging)
    provider_response = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    completed_at = Column(DateTime, nullable=True)
    
    def __repr__(self):
        return f"<PaymentTransaction(id={self.transaction_id}, amount={self.amount}, status={self.status})>"


def init_db():
    """Create all tables - call this once at startup"""
    try:
        Base.metadata.create_all(bind=engine)
        print("✅ Database tables created successfully")
    except Exception as e:
        print(f"❌ Failed to create database tables: {e}")
        raise


def get_db() -> Session:
    """
    Dependency function for FastAPI to get a database session
    
    Usage:
        @app.get("/payments")
        def get_payments(db: Session = Depends(get_db)):
            return db.query(PaymentTransaction).all()
    
    Yields:
        Session: SQLAlchemy database session
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()  # Rollback on error
        raise
    finally:
        db.close()


def get_db_stats() -> dict:
    """Get database connection pool statistics (for monitoring)"""
    return {
        "pool_size": engine.pool.size(),
        "checked_in": engine.pool.checkedin(),
        "overflow": engine.pool.overflow(),
        "total": engine.pool.total()
    }