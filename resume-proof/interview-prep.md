# Interview Preparation

## 2-Minute Project Walkthrough

> *"I built a payment idempotency proxy that sits between an application and a payment provider. The core problem is that payment providers aren't idempotent by default — the same request sent twice can result in two charges. This happens all the time due to network timeouts, double-clicks, and retry logic.*
>
> *My solution guarantees exactly-once processing. When a request arrives with an Idempotency-Key header, the proxy first checks Redis cache for a previous response (sub-millisecond). If not found, it checks PostgreSQL for an existing record. If still new, it acquires a distributed Redis lock, processes the payment, caches the result, and releases the lock.*
>
> *Any concurrent duplicate with the same key either hits the cache, finds the completed DB record, or gets a 409 conflict — but never a double charge. I proved this with a test that sends 10 concurrent identical requests and verified only 1 transaction in the database.*
>
> *I also built full observability — 20+ Prometheus metrics with a Grafana dashboard showing cache hit ratio, latency percentiles, lock contention, and financial volume. Everything runs in Docker Compose for one-command setup."*

---

## Common Interview Questions

### Q: Why Redis for caching AND locking? Why not just one?

> *They serve different purposes. Redis cache gives us sub-millisecond duplicate detection — a simple GET returns the cached response in ~1ms. The distributed lock serializes concurrent writes for the same key. If two requests arrive at the exact same time, the lock ensures only one processes and the other waits or gets a 409. Using separate mechanisms (cache vs lock) keeps each simple and independently testable.*

### Q: What happens if Redis goes down?

> *The health check detects it and marks the service as degraded, not down. The fallback path goes directly to PostgreSQL for existing records. However, we'd lose caching and lock-based concurrency protection — concurrent duplicates could both reach the payment provider. In production, Redis Sentinel or Cluster handles HA. For this project, Redis is a dependency with a health check in Docker Compose and auto-restart.*

### Q: How did you test concurrent requests?

> *I used Python's ThreadPoolExecutor to send 10 requests with the same idempotency key simultaneously. The test then verifies that exactly 1 payment transaction exists in PostgreSQL. I also verify that the other 9 requests returned either success (cache hit) or 409 (concurrent conflict). The test name is `test_concurrent_identical_requests`. It's been running consistently in our test suite.*

### Q: The tamper detection — how does it work?

> *We compute a SHA-256 hash of the request body and store it alongside the idempotency record. If a client reuses the same idempotency key with a different request body, the hash comparison catches it and returns a 409 with a clear error message. This prevents a subtle class of bugs where a client retries a different transaction with an old key.*

### Q: What would you improve for production?

> *Three things: (1) Swap sync Redis for redis.asyncio to avoid blocking the event loop on cache/lock operations. (2) Add structured JSON logging for better debugging. (3) Add rate limiting to prevent key brute-forcing. The architecture is modular enough that each of these changes is isolated to one file.*

### Q: How is this different from just using a database unique constraint?

> *A unique constraint on idempotency_key in PostgreSQL would work for simple cases, but it can't handle concurrent requests at the application level — both requests would fail the uniqueness check, roll back, and retry, potentially causing double charges. The distributed lock serializes writes in Redis (much faster than DB round-trips), and the cache provides sub-millisecond reads for the common case (duplicate detection). The DB is the source of truth, but Redis is the performance layer.*

---

## Whiteboard / System Design Talking Points

### Key Components

1. **FastAPI** — async route handlers, auto-docs at `/docs`
2. **Redis** — Response cache (SETEX with 24h TTL) + Distributed lock (SET NX + Lua script)
3. **PostgreSQL** — Idempotency records + Payment transactions (audit trail)
4. **Prometheus + Grafana** — 20+ metrics, real-time dashboard
5. **Docker Compose** — All services containerized

### Request Flow

```
Request → Validate UUID → Check Redis cache → Check PostgreSQL → 
Acquire lock → Double-check → Process payment → Store result → 
Cache response → Release lock → Return response
```

### Why This Architecture?

- **Layered approach**: Each layer (cache / DB / lock) handles different contention scenarios
- **Fast path**: 95%+ of duplicates resolve at the Redis cache level (~1ms)
- **Slow path**: First request goes through lock → DB → provider (still fast via async)
- **Tamper detection**: SHA-256 hash comparison on duplicate keys
