"""Distributed lock service using Redis"""

import redis
import uuid
import time
import asyncio
from typing import Optional, Callable, TypeVar, Any
import logging

logger = logging.getLogger(__name__)

T = TypeVar('T')


_LOCK_SCRIPT_SOURCE = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
"""


class RedisLock:
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    def _get_script(self):
        if not hasattr(RedisLock, '_lock_script'):
            RedisLock._lock_script = self.redis.register_script(_LOCK_SCRIPT_SOURCE)
        return RedisLock._lock_script
    
    def acquire(
        self, 
        lock_key: str, 
        timeout_seconds: int = 10,
        retry_seconds: float = 0.1,
        max_retries: int = 50
    ) -> Optional[str]:
        """Acquire lock with retry"""
        lock_value = str(uuid.uuid4())
        
        for attempt in range(max_retries):
            acquired = self.redis.set(
                lock_key,
                lock_value,
                nx=True,
                px=timeout_seconds * 1000
            )
            
            if acquired:
                logger.debug(f" Acquired lock: {lock_key} (attempt {attempt + 1})")
                return lock_value
            
            time.sleep(retry_seconds)
        
        logger.warning(f" Failed to acquire lock after {max_retries} attempts: {lock_key}")
        return None
    
    def release(self, lock_key: str, lock_value: str) -> bool:
        """Release lock if owned"""
        result = self._get_script()(keys=[lock_key], args=[lock_value])
        
        if result:
            logger.debug(f" Released lock: {lock_key}")
        else:
            logger.warning(f" Failed to release lock (not owner or already expired): {lock_key}")
        
        return bool(result)


class LockService:
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.lock = RedisLock(redis_client)
    
    def execute_with_lock(
        self,
        lock_key: str,
        func: Callable[[], T],
        timeout_seconds: int = 10,
        fail_fast: bool = False
    ) -> Optional[T]:
        """Execute function under distributed lock"""
        from app.metrics import metrics
        lock_value = self.lock.acquire(lock_key, timeout_seconds)
        
        if not lock_value:
            metrics.track_lock_failed()
            if fail_fast:
                raise Exception(f"Could not acquire lock for key: {lock_key}")
            return None
        
        metrics.track_lock_acquired()
        try:
            return func()
        finally:
            self.lock.release(lock_key, lock_value)
    
    async def execute_with_lock_async(
        self,
        lock_key: str,
        func: Callable[..., Any],
        timeout_seconds: int = 10,
        *args,
        **kwargs
    ) -> Any:
        lock_value = self.lock.acquire(lock_key, timeout_seconds)
        
        if not lock_value:
            raise Exception(f"Could not acquire lock for key: {lock_key}")
        
        try:
            if asyncio.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            else:
                return func(*args, **kwargs)
        finally:
            self.lock.release(lock_key, lock_value)