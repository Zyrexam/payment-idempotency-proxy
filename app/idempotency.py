"""
Core Idempotency Logic
This is the heart of our payment proxy
"""

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Tuple, Optional, Dict, Any
from sqlalchemy.orm import Session
import redis

from app.database import IdempotencyRecord, ProcessingStatus, PaymentTransaction
from app.locks import LockService
import logging

logger = logging.getLogger(__name__)


class IdempotencyService:
    """
    Core idempotency logic for payment processing
    
    Flow:
    1. Check Redis cache (fast path)
    2. Check PostgreSQL for existing record
    3. Acquire distributed lock
    4. Double-check (in case another request finished while waiting)
    5. Create processing record
    6. Call payment provider
    7. Store result
    8. Release lock
    """
    
    def __init__(self, redis_client: redis.Redis, db_session: Session):
        self.redis = redis_client
        self.db = db_session
        self.lock_service = LockService(redis_client)
        self.cache_ttl_hours = 24  # Cache responses for 24 hours
    
    def _hash_request(self, body: str) -> str:
        """Create SHA-256 hash of request body for tamper detection"""
        return hashlib.sha256(body.encode()).hexdigest()
    
    def _get_cached_response(self, idempotency_key: str) -> Optional[Tuple[int, str]]:
        """
        Check Redis cache for previous response
        This is the FAST path - Redis is in-memory
        """
        cached = self.redis.get(f"idempotency:response:{idempotency_key}")
        if cached:
            data = json.loads(cached)
            logger.info(f"💾 Cache HIT for key: {idempotency_key}")
            return data["status_code"], data["body"]
        
        logger.debug(f"💾 Cache MISS for key: {idempotency_key}")
        return None
    
    def _cache_response(self, idempotency_key: str, status_code: int, body: str):
        """Cache response in Redis for fast duplicate detection"""
        self.redis.setex(
            f"idempotency:response:{idempotency_key}",
            self.cache_ttl_hours * 3600,
            json.dumps({"status_code": status_code, "body": body})
        )
        logger.debug(f"💾 Cached response for key: {idempotency_key}")
    
    def _get_existing_record(self, idempotency_key: str) -> Optional[IdempotencyRecord]:
        """Query database for existing idempotency record"""
        return self.db.query(IdempotencyRecord).filter(
            IdempotencyRecord.idempotency_key == idempotency_key
        ).first()
    
    def _create_processing_record(
        self, 
        idempotency_key: str, 
        request_body: str,
        request_hash: str
    ) -> IdempotencyRecord:
        """Create a new record with PROCESSING status"""
        record = IdempotencyRecord(
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            request_body=request_body,
            status=ProcessingStatus.PROCESSING,
            expires_at=datetime.utcnow() + timedelta(days=1)
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        logger.info(f"📝 Created processing record for key: {idempotency_key}")
        return record
    
    def _update_record_completed(
        self, 
        record: IdempotencyRecord, 
        status_code: int, 
        response_body: str
    ):
        """Update record as completed"""
        record.status = ProcessingStatus.COMPLETED
        record.response_status_code = status_code
        record.response_body = response_body
        record.updated_at = datetime.utcnow()
        self.db.commit()
        logger.info(f"✅ Completed record for key: {record.idempotency_key}")
    
    def _update_record_failed(self, record: IdempotencyRecord, error: str):
        """Update record as failed"""
        record.status = ProcessingStatus.FAILED
        record.error_message = error
        record.updated_at = datetime.utcnow()
        self.db.commit()
        logger.error(f"❌ Failed record for key: {record.idempotency_key}, error: {error}")
    
    def _create_transaction_record(
        self,
        transaction_id: str,
        idempotency_key: str,
        amount: int,
        currency: str,
        status: str,
        provider_response: str
    ):
        """Create a permanent transaction record for successful payments"""
        transaction = PaymentTransaction(
            transaction_id=transaction_id,
            idempotency_key=idempotency_key,
            amount=amount,
            currency=currency,
            status=status,
            provider_response=provider_response,
            completed_at=datetime.utcnow() if status == "completed" else None
        )
        self.db.add(transaction)
        self.db.commit()
        logger.info(f"💰 Created transaction record: {transaction_id}")
    
    def process_payment(
        self,
        idempotency_key: str,
        request_body: str,
        payment_processor: callable
    ) -> Tuple[int, str]:
        """
        Main idempotent payment processing logic
        
        This is the most important method in the entire project.
        It guarantees exactly-once processing.
        
        Args:
            idempotency_key: Unique key from client
            request_body: JSON string of payment request
            payment_processor: Function that actually calls the payment provider
        
        Returns:
            Tuple of (status_code, response_body)
        """
        request_hash = self._hash_request(request_body)
        
        # ========== STEP 1: Check Cache (Fast Path) ==========
        cached = self._get_cached_response(idempotency_key)
        if cached:
            logger.info(f"🚀 Fast path: returning cached response for {idempotency_key}")
            return cached
        
        # ========== STEP 2: Check Database ==========
        existing = self._get_existing_record(idempotency_key)
        
        if existing:
            logger.info(f"📋 Found existing record for key: {idempotency_key}, status: {existing.status}")
            
            if existing.status == ProcessingStatus.COMPLETED:
                # Cache and return completed response
                self._cache_response(
                    idempotency_key, 
                    existing.response_status_code, 
                    existing.response_body
                )
                return existing.response_status_code, existing.response_body
            
            elif existing.status == ProcessingStatus.PROCESSING:
                # Another request is currently processing this key
                return 409, json.dumps({
                    "error": "Request is already being processed",
                    "idempotency_key": idempotency_key,
                    "status": "processing",
                    "message": "Please retry in a few seconds"
                })
            
            elif existing.status == ProcessingStatus.FAILED:
                # Previous request failed - client can retry with same key
                # But we don't automatically retry - client must decide
                return 500, json.dumps({
                    "error": "Previous request failed",
                    "idempotency_key": idempotency_key,
                    "status": "failed",
                    "message": "Please retry with a new idempotency key"
                })
        
        # ========== STEP 3: Execute with Distributed Lock ==========
        lock_key = f"idempotency:lock:{idempotency_key}"
        
        def execute_payment():
            """Inner function that runs under lock protection"""
            
            # Double-check after acquiring lock
            # (Another request might have completed while we were waiting)
            recheck = self._get_existing_record(idempotency_key)
            if recheck:
                if recheck.status == ProcessingStatus.COMPLETED:
                    logger.info(f"🔒 Double-check: found completed record after lock")
                    return recheck.response_status_code, recheck.response_body
                elif recheck.status == ProcessingStatus.PROCESSING:
                    return 409, json.dumps({
                        "error": "Request is being processed",
                        "idempotency_key": idempotency_key
                    })
            
            # Create processing record (this is the "first" request)
            record = self._create_processing_record(idempotency_key, request_body, request_hash)
            
            try:
                # Call the actual payment provider
                logger.info(f"💳 Calling payment processor for key: {idempotency_key}")
                status_code, response_body = payment_processor(request_body)
                
                # Parse response to extract transaction details
                response_data = json.loads(response_body)
                
                # Create permanent transaction record if successful
                if status_code == 200:
                    transaction_id = response_data.get("transaction_id")
                    request_data = json.loads(request_body)
                    amount = request_data.get("amount", 0)
                    currency = request_data.get("currency", "USD")
                    
                    self._create_transaction_record(
                        transaction_id=transaction_id,
                        idempotency_key=idempotency_key,
                        amount=int(amount * 100),  # Convert dollars to cents
                        currency=currency,
                        status="completed",
                        provider_response=response_body
                    )
                
                # Update idempotency record
                self._update_record_completed(record, status_code, response_body)
                
                # Cache response for future duplicate requests
                self._cache_response(idempotency_key, status_code, response_body)
                
                return status_code, response_body
                
            except Exception as e:
                error_msg = str(e)
                logger.exception(f"💥 Payment processing failed: {error_msg}")
                self._update_record_failed(record, error_msg)
                return 500, json.dumps({
                    "error": "Internal server error",
                    "details": error_msg,
                    "idempotency_key": idempotency_key
                })
        
        # Execute with lock - only one request at a time for this key
        result = self.lock_service.execute_with_lock(
            lock_key, 
            execute_payment, 
            timeout_seconds=30,
            fail_fast=True
        )
        
        if result is None:
            # Could not acquire lock (should be rare with fail_fast=True)
            return 409, json.dumps({
                "error": "Concurrent request detected",
                "idempotency_key": idempotency_key,
                "message": "Please retry"
            })
        
        return result