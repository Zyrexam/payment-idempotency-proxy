from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
import redis
import json
import uuid
from datetime import datetime, timezone
from typing import Optional
import logging
import time
from app.database import get_db, init_db, engine, PaymentTransaction, IdempotencyRecord
from contextlib import asynccontextmanager
from app.idempotency import IdempotencyService
from app.schemas import PaymentRequest
from app.locks import LockService
from app.metrics import metrics
from app.config import settings
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(" Starting Payment Idempotency Proxy v1.0.0")

    try:
        redis_client.ping()
        logger.info(" Connected to Redis")
        redis_connected()
    except Exception as e:
        logger.error(f" Redis connection failed: {e}")
        redis_connected()

    try:
        init_db()
        logger.info(" Database initialized")

        metrics.update_db_pool_stats(engine.pool.size(), 0)
    except Exception as e:
        logger.error(f" Database initialization failed: {e}")

    yield

    logger.info(" Shutting down...")
    redis_client.close()
    logger.info("Redis connection closed")


app = FastAPI(
    title="Payment Idempotency Proxy",
    description="Production-grade idempotent payment processing proxy",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600,  # Cache preflight for 1 hour
)

redis_client = redis.Redis(
    host=settings.redis_host,
    port=settings.redis_port,
    password=settings.redis_password,
    decode_responses=True,
    health_check_interval=30
)


def redis_connected() -> bool:
    try:
        redis_client.ping()
        metrics.redis_connected.set(1)
        return True
    except:
        metrics.redis_connected.set(0)
        return False

def validate_uuid(idempotency_key: str) -> bool:
    try:
        uuid.UUID(idempotency_key, version=4)
        return True
    except ValueError:
        return False


def mock_payment_provider(request_body: str) -> tuple[int, str]:
    time.sleep(0.05)
    
    data = json.loads(request_body)
    amount = data.get("amount", 0)

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

    metrics.track_payment_success(int(round(amount * 100)))
    
    return 200, json.dumps({
        "status": "succeeded",
        "transaction_id": transaction_id,
        "amount": amount,
        "currency": data.get("currency", "USD"),
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    })


@app.post("/api/v1/payments")
async def create_payment(
    request: Request,
    payment_request: PaymentRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db)
):
    """Create payment with idempotency support"""
    start_time = time.time()
    
    if not validate_uuid(idempotency_key):
        metrics.track_invalid_key()
        raise HTTPException(
            status_code=400,
            detail="Invalid Idempotency-Key format. Must be a valid UUID v4."
        )
    
    logger.info(f" Payment request - Key: {idempotency_key[:8]}..., Amount: ${payment_request.amount}")

    idempotency_service = IdempotencyService(redis_client, db)
    request_body = payment_request.model_dump_json()
    
    try:
        cache_start = time.time()
        status_code, response_body = idempotency_service.process_payment(
            idempotency_key=idempotency_key,
            request_body=request_body,
            payment_processor=mock_payment_provider
        )
        metrics.observe_cache_lookup(time.time() - cache_start)

        if status_code == 409:
            metrics.track_conflict()

        metrics.observe_request_duration(time.time() - start_time)
        
        logger.info(f" Response: {status_code} for key {idempotency_key[:8]}...")
        return JSONResponse(status_code=status_code, content=json.loads(response_body))
        
    except Exception as e:
        logger.exception(f" Payment processing error: {e}")
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
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )


@app.get("/redis/stats")
async def redis_stats():
    try:
        info = redis_client.info()
        # Use SCAN instead of KEYS for production safety
        cache_keys = 0
        cursor = 0
        while True:
            cursor, keys = redis_client.scan(
                cursor=cursor,
                match="idempotency:response:*",
                count=1000
            )
            cache_keys += len(keys)
            if cursor == 0:
                break
        memory_used = info.get('used_memory', 0)
        metrics.redis_cache_keys.set(cache_keys)
        metrics.redis_memory_usage.set(memory_used)
        metrics.redis_connected.set(1)
        
        # Compute hit ratio from Prometheus counters
        hits = metrics.idempotency_cache_hits._value.get()
        misses = metrics.idempotency_cache_misses._value.get()
        total = hits + misses
        hit_ratio = f"{hits/total*100:.1f}%" if total > 0 else "0.0%"

        return {
            "connected": True,
            "cache_keys": cache_keys,
            "memory_used_mb": round(memory_used / 1024 / 1024, 2),
            "redis_version": info.get('redis_version'),
            "uptime_seconds": info.get('uptime_in_seconds'),
            "hit_ratio": hit_ratio,
            "total_connections": info.get('total_connections_received'),
            "total_commands": info.get('total_commands_processed')
        }
    except Exception:
        metrics.redis_connected.set(0)
        return {"connected": False, "error": "Redis connection failed"}


@app.get("/health")
async def health_check():
    health_status = {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "service": "payment-idempotency-proxy",
        "version": "1.0.0"
    }

    dependencies = {}
    redis_ok = redis_connected()

    if redis_ok:
        dependencies["redis"] = "connected"
    else:
        dependencies["redis"] = "disconnected"
        health_status["status"] = "unhealthy"

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        dependencies["postgresql"] = "connected"
    except Exception:
        dependencies["postgresql"] = "disconnected"
        health_status["status"] = "unhealthy"

    health_status["dependencies"] = dependencies
    status_code = 200 if health_status["status"] == "healthy" else 503
    return JSONResponse(status_code=status_code, content=health_status)

@app.get("/")
async def root():
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
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Idempotency-Key",
            "Access-Control-Max-Age": "3600"
        }
    )