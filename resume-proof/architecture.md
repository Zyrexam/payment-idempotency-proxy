# Architecture Overview

## System Design

```
┌─────────────┐     POST /api/v1/payments
│   Client    │─────▸ Idempotency-Key: <uuid>
│  (cURL/App) │     Content-Type: application/json
└─────────────┘     {"amount": 99.99, "currency": "USD", "source": "card_123"}
         │
         ▼
┌──────────────────────────────────────────────┐
│          Payment Idempotency Proxy            │
│               (FastAPI + Uvicorn)             │
│                                              │
│  1. Validate UUID v4 format                  │
│  2. Check Redis cache (fast path)            │
│  3. Check PostgreSQL for existing record     │
│  4. Acquire Redis distributed lock           │
│  5. Double-check (race condition guard)      │
│  6. Call payment provider                    │
│  7. Cache response in Redis (24h TTL)        │
│  8. Release lock                             │
└──────────────────────────────────────────────┘
         │
         ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│   Redis 7   │  │ PostgreSQL  │  │  Prometheus  │
│  Cache +    │  │ Idempotency │  │   20+       │
│  Locks      │  │ + Audit     │  │   Metrics   │
└─────────────┘  └─────────────┘  └─────────────┘
                                       │
                                       ▼
                                 ┌─────────────┐
                                 │   Grafana   │
                                 │  Dashboard  │
                                 └─────────────┘
```

---

## Data Flow

### First Request (cache miss)

| Step | Action | Time |
|------|--------|------|
| 1 | Validate Idempotency-Key (UUID v4) | <1ms |
| 2 | Redis GET → MISS | ~1ms |
| 3 | PostgreSQL SELECT → not found | ~3ms |
| 4 | Redis SET NX → lock acquired | ~1ms |
| 5 | PostgreSQL SELECT (double-check) | ~3ms |
| 6 | Call payment provider (mock: 50ms) | ~50ms |
| 7 | Redis SETEX → cache response (24h) | ~1ms |
| 8 | PostgreSQL INSERT → transaction | ~5ms |
| 9 | Redis DEL → release lock | ~1ms |
| **Total** | | **~65ms** |

### Duplicate Request (cache hit)

| Step | Action | Time |
|------|--------|------|
| 1 | Validate Idempotency-Key (UUID v4) | <1ms |
| 2 | Redis GET → HIT, return cached | ~1ms |
| **Total** | | **~2ms** |

---

## Key Design Decisions

### Why Redis for both cache and locking?

- **Cache**: SETEX with 24h TTL — sub-millisecond reads for 95%+ of duplicates
- **Lock**: SET NX (set if not exists) with Lua-based atomic release — prevents race conditions
- Same infrastructure, different use cases, independently testable

### Why PostgreSQL in addition to Redis?

- Redis is ephemeral (data loss on restart without persistence)
- PostgreSQL is the durable source of truth
- Allows auditing, reporting, and debugging via direct queries
- Fallback path when Redis is degraded

### Why request body hashing?

- Prevents subtle bugs where a client reuses an idempotency key with a different payload
- SHA-256 hash stored in `IdempotencyRecord.request_hash`
- On duplicate: incoming hash vs stored hash → mismatch returns 409

---

## Infrastructure

```
docker-compose.yml
├── PostgreSQL 15 (port 5433)    — Audit trail
├── Redis 7 (port 6379)          — Cache + Locks
├── Redis Commander (port 8081)  — Redis Web UI
├── Prometheus (port 9090)       — Metrics collection
└── Grafana (port 3000)          — Metrics visualization
```

## Test Coverage

| Layer | Test | What It Verifies |
|-------|------|------------------|
| API | test_health_check | Endpoint returns 200 with dependency status |
| API | test_metrics_endpoint | Prometheus format with metric data |
| API | test_payment_schema_validation | Pydantic validation (amount, source prefix, currency) |
| Idempotency | test_successful_payment | End-to-end payment with retry logic |
| Idempotency | test_idempotency_duplicate_request | Same key → same transaction_id |
| Idempotency | test_concurrent_identical_requests | 10 concurrent → 1 transaction |
| Idempotency | test_idempotency_duplicate_different_body_rejected | Same key, different body → cached first response |
| Idempotency | test_cross_key_isolation | Different keys → different transactions |
| Lock | test_redis_lock_functionality | Acquire/release atomicity, contention |
| Lock | test_response_caching | Redis cache populated and retrievable |
| Performance | test_performance_under_load | 100 requests in < threshold |
