"""
Distributed Lock Service using Redis
Prevents race conditions when multiple requests with same key arrive simultaneously

How it works:
1. First request acquires lock -> processes payment
2. Second request tries same lock -> waits or fails fast
3. First request releases lock -> second request sees cached result
"""

import redis
import uuid
import time
import asyncio
from typing import Optional, Callable, TypeVar, Any
from functools import wraps
import logging

logger = logging.getLogger(__name__)

T = TypeVar('T')


class RedisLock:
    """
    Distributed lock implementation using Redis SET NX (set if not exists)
    
    The lock script ensures atomic release - we only release if we own the lock
    """
    
    # Lua script for atomic lock release
    # This runs ON the Redis server - no race conditions
    LOCK_SCRIPT = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        # Register the Lua script for faster execution
        self._lock_script = self.redis.register_script(self.LOCK_SCRIPT)
    
    def acquire(
        self, 
        lock_key: str, 
        timeout_seconds: int = 10,
        retry_seconds: float = 0.1,
        max_retries: int = 50
    ) -> Optional[str]:
        """
        Acquire a distributed lock
        
        Args:
            lock_key: Unique key for the lock (e.g., "idempotency:lock:key-123")
            timeout_seconds: How long the lock lasts (auto-release)
            retry_seconds: Time between retry attempts
            max_retries: Maximum number of retry attempts
        
        Returns:
            Lock value (unique token) if acquired, None otherwise
        
        Example:
            lock_value = lock.acquire("payment:123", timeout_seconds=30)
            if lock_value:
                try:
                    # Do work
                finally:
                    lock.release("payment:123", lock_value)
        """
        lock_value = str(uuid.uuid4())  # Unique token to identify our lock
        
        for attempt in range(max_retries):
            # SET key value NX PX milliseconds
            # NX = Only set if key does NOT exist
            # PX = Expiry in milliseconds
            acquired = self.redis.set(
                lock_key,
                lock_value,
                nx=True,           # Only if not exists
                px=timeout_seconds * 1000  # Auto-expire after timeout
            )
            
            if acquired:
                logger.debug(f"🔒 Acquired lock: {lock_key} (attempt {attempt + 1})")
                return lock_value
            
            # Wait before retrying
            time.sleep(retry_seconds)
        
        logger.warning(f"⚠️ Failed to acquire lock after {max_retries} attempts: {lock_key}")
        return None
    
    def release(self, lock_key: str, lock_value: str) -> bool:
        """
        Release a lock only if we own it
        
        Args:
            lock_key: The lock key
            lock_value: The value returned from acquire()
        
        Returns:
            True if lock was released, False otherwise
        """
        # Use Lua script to atomically check and delete
        result = self._lock_script(keys=[lock_key], args=[lock_value])
        
        if result:
            logger.debug(f"🔓 Released lock: {lock_key}")
        else:
            logger.warning(f"⚠️ Failed to release lock (not owner or already expired): {lock_key}")
        
        return bool(result)


class LockService:
    """
    Convenience wrapper for executing functions with a distributed lock
    """
    
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
        """
        Execute a function with a distributed lock
        
        Args:
            lock_key: Key for the distributed lock
            func: Function to execute (must be synchronous)
            timeout_seconds: Lock timeout
            fail_fast: If True, raise exception when lock not acquired
        
        Returns:
            Result of func, or None if lock not acquired
        
        Example:
            result = lock_service.execute_with_lock(
                "payment:123",
                lambda: process_payment(),
                timeout_seconds=30
            )
        """
        lock_value = self.lock.acquire(lock_key, timeout_seconds)
        
        if not lock_value:
            if fail_fast:
                raise Exception(f"Could not acquire lock for key: {lock_key}")
            return None
        
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
        """Async version of execute_with_lock"""
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