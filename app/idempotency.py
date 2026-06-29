"""Core idempotency logic"""

import hashlib
import json
import uuid
import time
from datetime import datetime, timedelta, timezone
from app.metrics import metrics
from typing import Tuple, Optional, Dict, Any
from sqlalchemy.orm import Session
import redis

from app.database import IdempotencyRecord, ProcessingStatus, PaymentTransaction
from app.locks import LockService
import logging

logger = logging.getLogger(__name__)


class IdempotencyService:
    """Core idempotency logic with Redis caching and distributed locks"""
    
    def __init__(self, redis_client: redis.Redis, db_session: Session):
        self.redis = redis_client
        self.db = db_session
        self.lock_service = LockService(redis_client)
        self.cache_ttl_hours = 24

    def _hash_request(self, body: str) -> str:
        return hashlib.sha256(body.encode()).hexdigest()

    def _cache_response(self, idempotency_key: str, status_code: int, body: str):
        self.redis.setex(
            f"idempotency:response:{idempotency_key}",
            self.cache_ttl_hours * 3600,
            json.dumps({"status_code": status_code, "body": body})
        )
        logger.debug(f"Cached response for key: {idempotency_key}")

    def _get_existing_record(self, idempotency_key: str) -> Optional[IdempotencyRecord]:
        return self.db.query(IdempotencyRecord).filter(
            IdempotencyRecord.idempotency_key == idempotency_key
        ).first()
    
    def _get_cached_response(self, idempotency_key: str) -> Optional[Tuple[int, str]]:
        start = time.time()
        cached = self.redis.get(f"idempotency:response:{idempotency_key}")
        metrics.observe_cache_lookup(time.time() - start)

        if cached:
            data = json.loads(cached)
            logger.debug(f" Cache HIT for key: {idempotency_key}")
            metrics.track_cache_hit()
            return data["status_code"], data["body"]

        logger.debug(f" Cache MISS for key: {idempotency_key}")
        metrics.track_cache_miss()
        return None

    def _create_processing_record(self, idempotency_key: str, request_body: str, request_hash: str) -> IdempotencyRecord:
        record = IdempotencyRecord(
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            request_body=request_body,
            status=ProcessingStatus.PROCESSING,
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        metrics.idempotency_record_creations.inc()
        logger.info(f"Created processing record for key: {idempotency_key}")
        return record

    def _update_record_completed(self, record: IdempotencyRecord, status_code: int, response_body: str):
        record.status = ProcessingStatus.COMPLETED
        record.response_status_code = status_code
        record.response_body = response_body
        record.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        self.db.commit()
        logger.info(f"Completed record for key: {record.idempotency_key}")

    def _update_record_failed(self, record: IdempotencyRecord, error: str):
        record.status = ProcessingStatus.FAILED
        record.error_message = error
        record.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        self.db.commit()
        logger.error(f"Failed record for key: {record.idempotency_key}, error: {error}")

    def _create_transaction_record(self, transaction_id: str, idempotency_key: str, amount: int, currency: str, status: str, provider_response: str):
        transaction = PaymentTransaction(
            transaction_id=transaction_id,
            idempotency_key=idempotency_key,
            amount=amount,
            currency=currency,
            status=status,
            provider_response=provider_response,
            completed_at=datetime.now(timezone.utc).replace(tzinfo=None) if status == "completed" else None
        )
        self.db.add(transaction)
        self.db.commit()
        logger.info(f"Created transaction record: {transaction_id}")

    def process_payment(
        self,
        idempotency_key: str,
        request_body: str,
        payment_processor: callable
    ) -> Tuple[int, str]:
        """Process payment with exactly-once guarantee"""
        request_hash = self._hash_request(request_body)

        cached = self._get_cached_response(idempotency_key)
        if cached:
            logger.info(f"Fast path: returning cached response for {idempotency_key}")
            return cached

        existing = self._get_existing_record(idempotency_key)

        if existing:
            logger.info(f"Found existing record for key: {idempotency_key}, status: {existing.status}")
            if existing.status == ProcessingStatus.COMPLETED:
                # Tamper detection: reject if request body hash differs
                if existing.request_hash != request_hash:
                    return 409, json.dumps({
                        "error": "Idempotency key reused with different request body",
                        "idempotency_key": idempotency_key,
                        "detail": "The provided Idempotency-Key was used with a different request body. Generate a new key for new requests."
                    })
                self._cache_response(
                    idempotency_key,
                    existing.response_status_code,
                    existing.response_body
                )
                return existing.response_status_code, existing.response_body

            elif existing.status == ProcessingStatus.PROCESSING:
                return 409, json.dumps({
                    "error": "Request is already being processed",
                    "idempotency_key": idempotency_key,
                    "status": "processing",
                    "message": "Please retry in a few seconds"
                })

            elif existing.status == ProcessingStatus.FAILED:
                return 500, json.dumps({
                    "error": "Previous request failed",
                    "idempotency_key": idempotency_key,
                    "status": "failed",
                    "message": "Please retry with a new idempotency key"
                })

        lock_key = f"idempotency:lock:{idempotency_key}"

        def execute_payment():
            # Double-check after acquiring lock
            recheck = self._get_existing_record(idempotency_key)
            if recheck:
                if recheck.status == ProcessingStatus.COMPLETED:
                    # Also check hash on double-check path
                    if recheck.request_hash != request_hash:
                        return 409, json.dumps({
                            "error": "Idempotency key reused with different request body",
                            "idempotency_key": idempotency_key
                        })
                    return recheck.response_status_code, recheck.response_body
                elif recheck.status == ProcessingStatus.PROCESSING:
                    return 409, json.dumps({
                        "error": "Request is being processed",
                        "idempotency_key": idempotency_key
                    })

            record = self._create_processing_record(idempotency_key, request_body, request_hash)

            try:
                logger.info(f"Calling payment processor for key: {idempotency_key}")
                status_code, response_body = payment_processor(request_body)
                response_data = json.loads(response_body)

                if status_code == 200:
                    transaction_id = response_data.get("transaction_id")
                    request_data = json.loads(request_body)
                    amount = request_data.get("amount", 0)
                    currency = request_data.get("currency", "USD")

                    self._create_transaction_record(
                        transaction_id=transaction_id,
                        idempotency_key=idempotency_key,
                        amount=int(round(amount * 100)),
                        currency=currency,
                        status="completed",
                        provider_response=response_body
                    )

                self._update_record_completed(record, status_code, response_body)
                self._cache_response(idempotency_key, status_code, response_body)
                return status_code, response_body

            except Exception as e:
                error_msg = str(e)
                self._update_record_failed(record, error_msg)
                return 500, json.dumps({
                    "error": "Internal server error",
                    "details": error_msg,
                    "idempotency_key": idempotency_key
                })

        result = self.lock_service.execute_with_lock(
            lock_key,
            execute_payment,
            timeout_seconds=30,
            fail_fast=True
        )

        if result is None:
            return 409, json.dumps({
                "error": "Concurrent request detected",
                "idempotency_key": idempotency_key,
                "message": "Please retry"
            })

        return result
