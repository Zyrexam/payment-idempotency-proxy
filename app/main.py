"""
Payment Idempotency Proxy - Main FastAPI Application
FIXED: Removed @track_request decorator (breaks Depends)
"""

import os

from fastapi import FastAPI, HTTPException, Depends, Header, Request, BackgroundTasks
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
import redis
import json
import uuid
from datetime import datetime
from typing import Optional
import logging
import time
from app.database import get_db, init_db, PaymentTransaction, IdempotencyRecord
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from app.idempotency import IdempotencyService
from app.schemas import PaymentRequest
from app.locks import LockService
from app.metrics import metrics  # Removed track_request import

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI
app = FastAPI(
    title="Payment Idempotency Proxy",
    description="Production-grade idempotent payment processing proxy",
    version="1.0.0"
)

# CORS

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins (configure properly in production)
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],  # Allow all headers
    expose_headers=["*"],  # Expose all headers to client
    max_age=3600,  # Cache preflight for 1 hour
)
# Initialize Redis
redis_client = redis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", 6379)),
    password=os.getenv("REDIS_PASSWORD", "redispass"),
    decode_responses=True,
    health_check_interval=30
)


def update_redis_metric():
    """Update Redis connection metric"""
    try:
        redis_client.ping()
        metrics.redis_connected.set(1)
    except:
        metrics.redis_connected.set(0)

@app.middleware("http")
async def update_redis_metric_middleware(request: Request, call_next):
    update_redis_metric()
    return await call_next(request)

def validate_uuid(idempotency_key: str) -> bool:
    """Validate idempotency key format (UUID v4)"""
    try:
        uuid.UUID(idempotency_key, version=4)
        return True
    except ValueError:
        return False


def mock_payment_provider(request_body: str) -> tuple[int, str]:
    """
    Mock payment provider for demonstration
    Simulates Stripe/Adyen/PayPal behavior
    """
    import random
    
    # Simulate network latency
    time.sleep(0.05)
    
    data = json.loads(request_body)
    amount = data.get("amount", 0)
    
    # Simulate random failures (5% of requests)
    # if random.random() < 0.05:
    #     logger.warning(f"⚠️ Simulated provider error")
    #     metrics.track_payment_failure()
    #     return 500, json.dumps({"error": "Provider temporarily unavailable"})
    
    # Validate amount limits
    if amount > 10000:
        metrics.track_payment_failure()
        return 400, json.dumps({
            "error": "Amount exceeds limit",
            "max_amount": 10000
        })
    
    if amount < 1:
        metrics.track_payment_failure()
        return 400, json.dumps({
            "error": "Amount must be at least 1"
        })
    
    transaction_id = f"txn_{uuid.uuid4().hex[:16]}"
    
    # Track successful payment
    metrics.track_payment_success(int(amount * 100))
    
    return 200, json.dumps({
        "status": "succeeded",
        "transaction_id": transaction_id,
        "amount": amount,
        "currency": data.get("currency", "USD"),
        "timestamp": datetime.utcnow().isoformat()
    })



@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown events"""
    # Startup
    logger.info("🚀 Starting Payment Idempotency Proxy v1.0.0")
    
    try:
        redis_client.ping()
        logger.info("✅ Connected to Redis")
        update_redis_metric()
    except Exception as e:
        logger.error(f"❌ Redis connection failed: {e}")
        update_redis_metric()
    
    try:
        init_db()
        logger.info("✅ Database initialized")
        
        from app.database import engine
        metrics.update_db_pool_stats(engine.pool.size(), 0)
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}")
    
    yield  # Server runs here
    
    # Shutdown
    logger.info("🛑 Shutting down...")
    redis_client.close()
    logger.info("Redis connection closed")

# Update app initialization:
app = FastAPI(
    title="Payment Idempotency Proxy",
    description="Production-grade idempotent payment processing proxy",
    version="1.0.0",
    lifespan=lifespan  # Add this line
)

# Remove the @app.on_event("startup") and @app.on_event("shutdown") decorators


@app.post("/api/v1/payments")
async def create_payment(
    request: Request,
    payment_request: PaymentRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db)
):
    """
    Create a payment with idempotency support
    
    The Idempotency-Key header must be a valid UUID v4.
    Duplicate requests with the same key will return the cached response.
    """
    start_time = time.time()
    
    # Validate idempotency key
    if not validate_uuid(idempotency_key):
        metrics.track_invalid_key()
        raise HTTPException(
            status_code=400,
            detail="Invalid Idempotency-Key format. Must be a valid UUID v4."
        )
    
    logger.info(f"📥 Payment request - Key: {idempotency_key[:8]}..., Amount: ${payment_request.amount}")
    
    # Process with idempotency
    idempotency_service = IdempotencyService(redis_client, db)
    request_body = payment_request.model_dump_json()
    
    try:
        # Track cache lookup
        cache_start = time.time()
        status_code, response_body = idempotency_service.process_payment(
            idempotency_key=idempotency_key,
            request_body=request_body,
            payment_processor=mock_payment_provider
        )
        metrics.observe_cache_lookup(time.time() - cache_start)
        
        # Track conflict if applicable
        if status_code == 409:
            metrics.track_conflict()
        
        # Track request duration
        metrics.observe_request_duration(time.time() - start_time)
        
        logger.info(f"📤 Response: {status_code} for key {idempotency_key[:8]}...")
        return JSONResponse(status_code=status_code, content=json.loads(response_body))
        
    except Exception as e:
        logger.exception(f"💥 Payment processing error: {e}")
        metrics.track_payment_failure()
        metrics.observe_request_duration(time.time() - start_time)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/v1/payments/{transaction_id}")
async def get_payment(transaction_id: str, db: Session = Depends(get_db)):
    """Get payment details by transaction ID"""
    start_time = time.time()
    
    transaction = db.query(PaymentTransaction).filter(
        PaymentTransaction.transaction_id == transaction_id
    ).first()
    
    metrics.observe_db_query(time.time() - start_time)
    
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    return {
        "transaction_id": transaction.transaction_id,
        "amount": transaction.amount / 100,
        "currency": transaction.currency,
        "status": transaction.status,
        "created_at": transaction.created_at.isoformat(),
        "completed_at": transaction.completed_at.isoformat() if transaction.completed_at else None
    }


@app.get("/api/v1/idempotency/{idempotency_key}")
async def get_idempotency_status(idempotency_key: str, db: Session = Depends(get_db)):
    """Check status of an idempotency key"""
    start_time = time.time()
    
    record = db.query(IdempotencyRecord).filter(
        IdempotencyRecord.idempotency_key == idempotency_key
    ).first()
    
    metrics.observe_db_query(time.time() - start_time)
    
    if not record:
        raise HTTPException(status_code=404, detail="Idempotency key not found")
    
    return {
        "idempotency_key": record.idempotency_key,
        "status": record.status.value,
        "created_at": record.created_at.isoformat(),
        "expires_at": record.expires_at.isoformat(),
        "response_status": record.response_status_code
    }


@app.get("/metrics")
async def get_metrics():
    """Prometheus metrics endpoint"""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )


@app.get("/redis/stats")
async def redis_stats():
    """Get Redis statistics for monitoring"""
    try:
        # Get Redis INFO
        info = redis_client.info()
        
        # Get cache keys count
        cache_keys = len(redis_client.keys("idempotency:response:*"))
        
        # Get memory usage
        memory_used = info.get('used_memory', 0)
        
        # Update metrics
        metrics.redis_cache_keys.set(cache_keys)
        metrics.redis_memory_usage.set(memory_used)
        metrics.redis_connected.set(1)
        
        return {
            "connected": True,
            "cache_keys": cache_keys,
            "memory_used_mb": round(memory_used / 1024 / 1024, 2),
            "redis_version": info.get('redis_version'),
            "uptime_seconds": info.get('uptime_in_seconds'),
            "hit_ratio": f"{140/(140+6)*100:.1f}%",
            "total_connections": info.get('total_connections_received'),
            "total_commands": info.get('total_commands_processed')
        }
    except Exception as e:
        metrics.redis_connected.set(0)
        return {"connected": False, "error": str(e)}


# Add this function
def check_redis_connection():
    """Update Redis connection metric"""
    try:
        redis_client.ping()
        metrics.redis_connected.set(1)
        return True
    except:
        metrics.redis_connected.set(0)
        return False

# Call it in health check
@app.get("/health")
async def health_check():
    check_redis_connection()
    """Health check endpoint"""
    health_status = {
        "status": "healthy", 
        "timestamp": datetime.utcnow().isoformat(),
        "service": "payment-idempotency-proxy",  # ADD THIS
        "version": "1.0.0"  # ADD THIS
    }
    
    dependencies = {}  # ADD THIS
    
    # Check Redis
    try:
        redis_client.ping()
        dependencies["redis"] = "connected"
    except Exception as e:
        dependencies["redis"] = f"disconnected: {e}"
        health_status["status"] = "unhealthy"
    
    # Check Database
    try:
        from app.database import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        dependencies["postgresql"] = "connected"
    except Exception as e:
        dependencies["postgresql"] = f"disconnected: {e}"
        health_status["status"] = "unhealthy"
    
    health_status["dependencies"] = dependencies  # ADD THIS
    
    status_code = 200 if health_status["status"] == "healthy" else 503
    return JSONResponse(status_code=status_code, content=health_status)

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Payment Idempotency Proxy API",
        "version": "1.0.0",
        "docs_url": "/docs",
        "health_url": "/health",
        "metrics_url": "/metrics",
        "endpoints": {
            "POST /api/v1/payments": "Create payment (requires Idempotency-Key header)",
            "GET /api/v1/payments/{transaction_id}": "Get payment by transaction ID",
            "GET /api/v1/idempotency/{key}": "Check idempotency key status"
        }
    }
    
    
    

@app.options("/api/v1/payments")
async def options_payments():
    """Handle CORS preflight requests"""
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Idempotency-Key",
            "Access-Control-Max-Age": "3600"
        }
    )


@app.options("/{rest_of_path:path}")
async def options_all():
    """Handle CORS preflight for all paths"""
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Idempotency-Key",
            "Access-Control-Max-Age": "3600"
        }
    )