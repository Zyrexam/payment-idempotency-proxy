<div align="center">

# 💳 Payment Idempotency Proxy

### *Never Charge a Customer Twice Again*

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D?style=for-the-badge&logo=redis&logoColor=white)](https://redis.io)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://postgresql.org)
[![Prometheus](https://img.shields.io/badge/Prometheus-E6522C?style=for-the-badge&logo=prometheus&logoColor=white)](https://prometheus.io)
[![Grafana](https://img.shields.io/badge/Grafana-F46800?style=for-the-badge&logo=grafana&logoColor=white)](https://grafana.com)
[![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://docker.com)

**Production-grade distributed idempotency layer for payment systems.**

Prevents duplicate payment processing via Redis-backed distributed locks, response caching, and a PostgreSQL audit trail.

</div>


---

##  The Problem

Network timeouts, double-clicks, and retry logic all share the same root cause: payment providers are **not idempotent by default**. The same request sent twice can result in two charges.

This proxy sits between your application and a payment provider, guaranteeing that each unique `Idempotency-Key` results in exactly one payment — no matter how many times you retry or how many concurrent requests arrive.

---

##  How It Works

```
Request + Idempotency-Key
        │
        ▼
┌──────────────────┐     ┌──────────────┐
│  Validate UUID   │────▶│  Check Redis │
│  format (v4)     │     │  cache       │
└──────────────────┘     └──────┬───────┘
                                │
                     ╭──────────┴──────────╮
                     │ HIT                 │ MISS
                     ▼                     ▼
              ┌──────────────┐     ┌──────────────┐
              │ Return cached│     │  Check       │
              │ response     │     │  PostgreSQL  │
              └──────────────┘     └──────┬───────┘
                                          │
                               ╭──────────┴──────────╮
                               │ Existing            │ New
                               ▼                     ▼
                        ┌──────────────┐     ┌──────────────┐
                        │ Return stored│     │ Acquire      │
                        │ response     │     │ Redis lock   │
                        └──────────────┘     └──────┬───────┘
                                                    ▼
                                             ┌──────────────┐
                                             │ Process with │
                                             │ payment      │
                                             │ provider     │
                                             └──────┬───────┘
                                                    ▼
                                             ┌──────────────┐
                                             │ Cache result │
                                             │ + release    │
                                             │ lock         │
                                             └──────────────┘
```

**Key guarantees:**
- **Exactly-once processing** — distributed locks prevent race conditions
- **Sub-millisecond duplicate detection** — Redis response cache
- **Tamper detection** — request body hashing catches key reuse with different payloads
- **Complete audit trail** — PostgreSQL records every request lifecycle
- **Real-time monitoring** — Prometheus metrics + Grafana dashboard

---

##  Project Structure

```
payment-idempotency-proxy/
├── app/                         # Core application
│   ├── main.py                  # FastAPI app, routes, mock payment provider
│   ├── idempotency.py           # IdempotencyService — exactly-once logic
│   ├── database.py              # SQLAlchemy models, connection pool
│   ├── locks.py                 # RedisLock + LockService (distributed locks)
│   ├── metrics.py               # Prometheus metrics definitions
│   └── schemas.py               # Pydantic request/response schemas
├── config/
│   ├── prometheus.yml           # Prometheus scrape configuration
│   └── garphana_dashboard.json  # Grafana dashboard (importable)
├── scripts/
│   ├── generate_traffic.py      # Simulates realistic payment traffic
│   └── steady_load.py           # Steady load generator for clean graphs
├── tests/
│   ├── test_api.py              # API-level tests (health, schemas, CORS)
│   ├── test_idempotency.py      # Idempotency tests (concurrency, caching, locks)
│   └── test_redis_performance.py # Redis cache/lock benchmark script
├── docker-compose.yml           # PostgreSQL, Redis, Redis Commander, Prometheus, Grafana
├── run.py                       # Uvicorn entry point
├── requirements.txt             # Python dependencies
└── README.md
```

---

##  Quick Start

### Prerequisites

- Python 3.11+
- Docker & Docker Compose

### Setup (5 minutes)

```bash
# 1. Clone & enter
git clone https://github.com/yourusername/payment-idempotency-proxy
cd payment-idempotency-proxy

# 2. Create virtual environment
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
# source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Start infrastructure (PostgreSQL, Redis, Prometheus, Grafana)
docker-compose up -d

# 5. Run tests to verify
pytest tests/ -v

# 6. Start the proxy (database tables are auto-created on startup)
python run.py
```

### Verify It's Working

```bash
# Health check
curl http://localhost:8000/health

# Expected:
# {
#   "status": "healthy",
#   "service": "payment-idempotency-proxy",
#   "version": "1.0.0",
#   "dependencies": {
#     "redis": "connected",
#     "postgresql": "connected"
#   }
# }
```

### Your First Payment

```bash
# Generate a UUID v4 key
IDEMPOTENCY_KEY=$(uuidgen)   # or use Python: python -c "import uuid; print(uuid.uuid4())"

# Send payment
curl -X POST http://localhost:8000/api/v1/payments \
  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
  -H "Content-Type: application/json" \
  -d '{"amount": 99.99, "currency": "USD", "source": "card_123"}'

# Duplicate request — returns cached response, no double charge!
curl -X POST http://localhost:8000/api/v1/payments \
  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
  -H "Content-Type: application/json" \
  -d '{"amount": 99.99, "currency": "USD", "source": "card_123"}'
```

---

##  API Reference

### POST `/api/v1/payments`

The main endpoint. Requires a valid UUID v4 `Idempotency-Key` header.

| Header | Required | Format | Example |
|--------|----------|--------|---------|
| `Idempotency-Key` | Yes | UUID v4 | `123e4567-e89b-12d3-a456-426614174000` |
| `Content-Type` | Yes | `application/json` | — |

**Request body:**

```json
{
  "amount": 99.99,               // Required, 0.01–10,000
  "currency": "USD",             // Optional, default "USD"
  "source": "card_123456",       // Required, prefix: card_, bank_, crypto_
  "description": "Order #1234",  // Optional, max 255 chars
  "metadata": {                  // Optional
    "customer_id": "cust_123",
    "order_id": "ord_456"
  }
}
```

**Responses:**

| Status | Meaning |
|--------|---------|
| `200` | Success (first request or cache hit) |
| `400` | Invalid Idempotency-Key format |
| `409` | Request is being processed by another instance |
| `422` | Validation error (amount, source, etc.) |
| `500` | Provider error |

**200 response:**

```json
{
  "status": "succeeded",
  "transaction_id": "txn_abc123def456",
  "amount": 99.99,
  "currency": "USD",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

### GET `/api/v1/payments/{transaction_id}`

Retrieve payment details.

### GET `/api/v1/idempotency/{idempotency_key}`

Debug endpoint to check the status of an idempotency key.

### GET `/health`

Kubernetes-style health check with dependency status.

### GET `/metrics`

Prometheus metrics endpoint (scraped by Prometheus).

### GET `/redis/stats`

Live Redis statistics: cache key count, memory usage, version, uptime, hit ratio.

### GET `/`

Root endpoint with API overview and available endpoints.

---

##  Distributed Locking

The proxy uses Redis SET NX (set if not exists) with Lua-based atomic release to serialize concurrent requests with the same idempotency key.

```mermaid
sequenceDiagram
    participant A as Instance A
    participant B as Instance B
    participant Redis
    participant PG as PostgreSQL
    participant Provider as Payment Provider

    A->>Redis: SET NX lock:key-123
    Redis-->>A: OK (acquired)
    A->>PG: INSERT status=processing
    A->>Provider: Charge $99.99
    Provider-->>A: txn_abc123
    A->>PG: UPDATE status=completed
    A->>Redis: SETEX cache:key-123 (24h)
    A->>Redis: DEL lock:key-123

    B->>Redis: SET NX lock:key-123
    Redis-->>B: nil (not acquired)
    B->>Redis: GET cache:key-123
    Redis-->>B: cached response
    Note over B: Returns cached result — no charge
```

**Lock features:**
- Auto-expiry (prevents deadlocks)
- Retry with backoff
- Lua-scripted atomic release (safety check on ownership)
- `fail_fast` mode for immediate rejection

---

##  Observability

### Prometheus Metrics (20+ metrics)

| Metric | Type | Description |
|--------|------|-------------|
| `idempotency_successful_payments_total` | Counter | Successful payments |
| `idempotency_failed_payments_total` | Counter | Failed payments |
| `idempotency_cache_hits_total` | Counter | Cache hits (duplicate requests) |
| `idempotency_cache_misses_total` | Counter | Cache misses (first requests) |
| `idempotency_lock_acquisitions_total` | Counter | Distributed lock acquisitions |
| `idempotency_lock_failures_total` | Counter | Lock acquisition failures |
| `idempotency_request_duration_seconds` | Histogram | End-to-end request latency |
| `idempotency_cache_lookup_seconds` | Histogram | Redis cache lookup latency |
| `idempotency_db_query_seconds` | Histogram | Database query latency |
| `idempotency_redis_connected` | Gauge | Redis connection status (0/1) |
| `idempotency_total_amount_processed_cents` | Counter | Total monetary volume (cents) |

### Grafana Dashboard

An importable Grafana dashboard is at `config/garphana_dashboard.json`.

Access Grafana at `http://localhost:3000` (default: `admin`/`admin`).

**To import:** Log in to Grafana → **Create** → **Import** → Upload the JSON file or paste its contents.

**Dashboard panels include:**
- Payment rate (success/failure per second)
- Cache hit ratio
- Request latency (P50/P95/P99)
- Lock acquisition rate
- Database connection pool usage
- Redis memory & cache key count
- Financial volume (total amount processed)

---

##  Infrastructure

All services are defined in `docker-compose.yml`:

| Service | Port | Purpose |
|---------|------|---------|
| FastAPI Proxy | `:8000` | Payment idempotency API |
| PostgreSQL | `:5433` | Audit trail (mapped to 5433 to avoid local conflicts) |
| Redis | `:6379` | Response cache + distributed locks |
| Redis Commander | `:8081` | Web UI for inspecting Redis |
| Prometheus | `:9090` | Metrics collection |
| Grafana | `:3000` | Metrics visualization |

---

##  Generating Traffic

Two scripts are provided for load testing and demo purposes:

```bash
# Generate 50 random payments + concurrent duplicate test
python scripts/generate_traffic.py

# Steady stream (1 req every 0.5s) for clean Grafana graphs
python scripts/steady_load.py
```

---

##  Testing

```bash
# Run all tests
pytest tests/ -v

# With coverage
pytest tests/ -v --cov=app --cov-report=html

# Redis cache/lock performance benchmark
python tests/test_redis_performance.py
```

### Test coverage

| Test File | Tests | What It Verifies |
|-----------|-------|------------------|
| `test_api.py` | 4 | Health check, root endpoint, metrics endpoint, schema validation |
| `test_idempotency.py` | 21 | Concurrent identical requests, cache hits, lock functionality, cross-key isolation, tamper detection, CORS, response caching, performance under load |
| `test_redis_performance.py` | Manual (standalone script) | Cache hit ratio, concurrent cache access, distributed lock contention |

Key guarantee: **20 concurrent requests with the same idempotency key → 1 transaction in the database.**

---

##  Configuration

Environment variables (with defaults):

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://admin:password@localhost/idempotency` | PostgreSQL connection string |
| `REDIS_HOST` | `localhost` | Redis host |
| `REDIS_PORT` | `6379` | Redis port |
| `REDIS_PASSWORD` | `redispass` | Redis password |

---

##  Development

```bash
# Hot-reload server (auto-restarts on changes)
python run.py

# Database models are auto-created at startup via init_db()
# No migration tool required for development
```

---

##  Stack

- **Python 3.11** — type hints throughout
- **FastAPI** — async request handling, auto-generated OpenAPI docs at `/docs`
- **SQLAlchemy 2.0** — ORM with connection pooling
- **Redis 7** — caching (SETEX) + distributed locking (SET NX + Lua)
- **PostgreSQL 15** — durable audit trail
- **Prometheus + Grafana** — metrics and visualization
- **Docker** — containerized infrastructure
