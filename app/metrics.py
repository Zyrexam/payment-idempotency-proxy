"""
Prometheus Metrics Configuration
Tracks all important metrics for monitoring and alerting
"""

from prometheus_client import Counter, Histogram, Gauge, Info
import time
from functools import wraps
from typing import Callable
import logging

logger = logging.getLogger(__name__)


class Metrics:
    """
    Centralized metrics collection for the idempotency proxy
    
    Types of metrics:
    - Counter: Only increases (requests, errors)
    - Histogram: Measures distributions (latency)
    - Gauge: Goes up and down (cache size, connections)
    - Info: Static information (version)
    """
    
    def __init__(self):
        # ============ REQUEST METRICS ============
        
        self.payment_requests_total = Counter(
            'idempotency_payment_requests_total',
            'Total number of payment requests received',
            ['status']  # Label: success, failure, conflict
        )
        
        self.successful_payments_total = Counter(
            'idempotency_successful_payments_total',
            'Total number of successfully processed payments'
        )
        
        self.failed_payments_total = Counter(
            'idempotency_failed_payments_total',
            'Total number of failed payment requests'
        )
        
        self.conflicts_total = Counter(
            'idempotency_conflicts_total',
            'Total number of requests blocked due to idempotency conflict'
        )
        
        self.invalid_keys_total = Counter(
            'idempotency_invalid_keys_total',
            'Total number of invalid idempotency key format errors'
        )
        
        # ============ IDEMPOTENCY METRICS ============
        
        self.idempotency_cache_hits = Counter(
            'idempotency_cache_hits_total',
            'Total number of idempotency cache hits (duplicate requests)'
        )
        
        self.idempotency_cache_misses = Counter(
            'idempotency_cache_misses_total',
            'Total number of idempotency cache misses (first-time requests)'
        )
        
        self.idempotency_record_creations = Counter(
            'idempotency_record_creations_total',
            'Total number of new idempotency records created'
        )
        
        # Current cache size (up/down)
        self.cache_size = Gauge(
            'idempotency_cache_size',
            'Current number of cached idempotency responses'
        )
        
        # ============ REDIS METRICS ============
        
        self.redis_connected = Gauge(
            'idempotency_redis_connected',
            'Redis connection status (1=connected, 0=disconnected)'
        )
        
        self.redis_operation_duration = Histogram(
            'idempotency_redis_operation_seconds',
            'Redis operation latency',
            ['operation'],  # get, set, delete, lock_acquire, lock_release
            buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5]
        )
        
        self.redis_cache_keys = Gauge(
            'idempotency_redis_cache_keys',
            'Number of cached idempotency responses in Redis'
        )
        
        self.redis_memory_usage = Gauge(
            'idempotency_redis_memory_bytes',
            'Redis memory usage in bytes'
        )

        
        # ============ LOCK METRICS ============
        
        self.lock_acquisitions = Counter(
            'idempotency_lock_acquisitions_total',
            'Total number of distributed lock acquisitions'
        )
        
        self.lock_failures = Counter(
            'idempotency_lock_failures_total',
            'Total number of distributed lock acquisition failures'
        )
        
        self.lock_hold_duration = Histogram(
            'idempotency_lock_hold_seconds',
            'How long locks are held',
            buckets=[0.01, 0.05, 0.1, 0.5, 1, 2, 5]
        )
        
        # ============ LATENCY METRICS ============
        
        self.request_duration = Histogram(
            'idempotency_request_duration_seconds',
            'Total request processing duration (end-to-end)',
            buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5]
        )
        
        self.payment_processing_duration = Histogram(
            'idempotency_payment_processing_seconds',
            'Payment provider processing duration (excluding idempotency overhead)',
            buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1]
        )
        
        self.cache_lookup_duration = Histogram(
            'idempotency_cache_lookup_seconds',
            'Time spent checking Redis cache',
            buckets=[0.001, 0.005, 0.01, 0.05, 0.1]
        )
        
        self.db_query_duration = Histogram(
            'idempotency_db_query_seconds',
            'Time spent on database queries',
            buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5]
        )
        
        # ============ DATABASE METRICS ============
        
        self.db_pool_size = Gauge(
            'idempotency_db_pool_size',
            'Current database connection pool size'
        )
        
        self.db_pool_checked_out = Gauge(
            'idempotency_db_pool_checked_out',
            'Number of database connections currently checked out'
        )
        
        # ============ BUSINESS METRICS ============
        
        self.total_amount_processed = Counter(
            'idempotency_total_amount_processed_cents',
            'Total monetary amount processed (in cents)'
        )
        
        self.average_payment_amount = Gauge(
            'idempotency_average_payment_amount_cents',
            'Rolling average payment amount (in cents)'
        )
        
        # Running total for average calculation
        self._amount_sum = 0
        self._payment_count = 0
        
        # ============ SERVICE INFO ============
        
        self.service_info = Info('idempotency_service', 'Service information')
        self.service_info.info({
            'version': '1.0.0',
            'environment': 'production'
        })
    
    def track_cache_hit(self):
        """Record a cache hit"""
        self.idempotency_cache_hits.inc()
    
    def track_cache_miss(self):
        """Record a cache miss"""
        self.idempotency_cache_misses.inc()
    
    def track_lock_acquired(self, duration_seconds: float = None):
        """Record a successful lock acquisition"""
        self.lock_acquisitions.inc()
        if duration_seconds:
            self.lock_hold_duration.observe(duration_seconds)
    
    def track_lock_failed(self):
        """Record a lock acquisition failure"""
        self.lock_failures.inc()
    
    def track_payment_success(self, amount_cents: int):
        """Record a successful payment"""
        self.successful_payments_total.inc()
        self.payment_requests_total.labels(status='success').inc()
        self.total_amount_processed.inc(amount_cents)
        
        # Update rolling average
        self._amount_sum += amount_cents
        self._payment_count += 1
        self.average_payment_amount.set(self._amount_sum / self._payment_count)
    
    def track_payment_failure(self):
        """Record a failed payment"""
        self.failed_payments_total.inc()
        self.payment_requests_total.labels(status='failure').inc()
    
    def track_conflict(self):
        """Record a conflict (concurrent request)"""
        self.conflicts_total.inc()
        self.payment_requests_total.labels(status='conflict').inc()
    
    def track_invalid_key(self):
        """Record an invalid idempotency key"""
        self.invalid_keys_total.inc()
        self.payment_requests_total.labels(status='invalid').inc()
    
    def update_cache_size(self, size: int):
        """Update the current cache size gauge"""
        self.cache_size.set(size)
    
    def update_db_pool_stats(self, pool_size: int, checked_out: int):
        """Update database connection pool statistics"""
        self.db_pool_size.set(pool_size)
        self.db_pool_checked_out.set(checked_out)
    
    def observe_request_duration(self, duration: float):
        """Observe total request duration"""
        self.request_duration.observe(duration)
    
    def observe_payment_duration(self, duration: float):
        """Observe payment provider duration"""
        self.payment_processing_duration.observe(duration)
    
    def observe_cache_lookup(self, duration: float):
        """Observe cache lookup duration"""
        self.cache_lookup_duration.observe(duration)
    
    def observe_db_query(self, duration: float):
        """Observe database query duration"""
        self.db_query_duration.observe(duration)


# Create global metrics instance
metrics = Metrics()


def track_request(func: Callable):
    """Decorator to automatically track request duration"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = await func(*args, **kwargs)
            return result
        finally:
            duration = time.time() - start_time
            metrics.observe_request_duration(duration)
    return wrapper


def track_payment_processing(func: Callable):
    """Decorator to track payment provider duration"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            duration = time.time() - start_time
            metrics.observe_payment_duration(duration)
    return wrapper