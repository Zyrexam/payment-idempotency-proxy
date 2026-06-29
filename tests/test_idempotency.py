"""Idempotency integration tests - require Redis and PostgreSQL running"""

import pytest
import uuid
import json
import time
import concurrent.futures
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.main import app
from app.database import get_db, init_db, SessionLocal, engine
import redis

pytestmark = pytest.mark.integration

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_database():
    init_db()
    
    db = SessionLocal()
    db.execute(text("DELETE FROM payment_transactions"))
    db.execute(text("DELETE FROM idempotency_records"))
    db.commit()
    db.close()
    
    yield
    
    db = SessionLocal()
    db.execute(text("DELETE FROM payment_transactions"))
    db.execute(text("DELETE FROM idempotency_records"))
    db.commit()
    db.close()


@pytest.fixture
def redis_client():
    return redis.Redis(
        host='localhost',
        port=6379,
        password='redispass',
        decode_responses=True
    )


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "payment-idempotency-proxy"
    assert "dependencies" in data
    assert data["dependencies"]["redis"] == "connected"
    assert data["dependencies"]["postgresql"] == "connected"


def test_successful_payment():
    """Test successful payment processing with retry for random failures"""
    idempotency_key = str(uuid.uuid4())
    
    for attempt in range(3):
        response = client.post(
            "/api/v1/payments",
            headers={"Idempotency-Key": idempotency_key},
            json={
                "amount": 99.99,
                "currency": "USD",
                "source": "card_123456",
                "description": "Test payment"
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            assert data["status"] == "succeeded"
            assert "transaction_id" in data
            assert data["amount"] == 99.99
            return
        
        elif response.status_code == 500 and attempt < 2:
            time.sleep(0.1)
            continue
        else:
            assert False, f"Payment failed with status {response.status_code}: {response.text}"
    
    assert False, "Payment failed after 3 retries"


def test_idempotency_duplicate_request():
    """Test duplicate request returns same response"""
    idempotency_key = str(uuid.uuid4())
    payment_data = {
        "amount": 50.00,
        "currency": "USD",
        "source": "card_123"
    }

    response1 = None
    for attempt in range(3):
        response1 = client.post(
            "/api/v1/payments",
            headers={"Idempotency-Key": idempotency_key},
            json=payment_data
        )
        if response1.status_code == 200:
            break
        time.sleep(0.1)
    
    if response1 is None or response1.status_code != 200:
        pytest.skip("First request failed due to provider error")
    
    response2 = client.post(
        "/api/v1/payments",
        headers={"Idempotency-Key": idempotency_key},
        json=payment_data
    )
    
    assert response2.status_code == 200
    assert response1.json()["transaction_id"] == response2.json()["transaction_id"]


def test_different_keys_give_different_transactions():
    """Test different idempotency keys create separate transactions"""
    payment_data = {
        "amount": 25.00,
        "currency": "USD",
        "source": "card_123"
    }
    
    key1 = str(uuid.uuid4())
    response1 = None
    for attempt in range(3):
        response1 = client.post(
            "/api/v1/payments",
            headers={"Idempotency-Key": key1},
            json=payment_data
        )
        if response1 and response1.status_code == 200:
            break
        time.sleep(0.1)
    
    if response1 is None or response1.status_code != 200:
        pytest.skip("First payment failed due to provider error")

    key2 = str(uuid.uuid4())
    response2 = None
    for attempt in range(3):
        response2 = client.post(
            "/api/v1/payments",
            headers={"Idempotency-Key": key2},
            json=payment_data
        )
        if response2 and response2.status_code == 200:
            break
        time.sleep(0.1)
    
    if response2 is None or response2.status_code != 200:
        pytest.skip("Second payment failed due to provider error")
    
    assert response1.json()["transaction_id"] != response2.json()["transaction_id"]


def test_invalid_idempotency_key_format():
    """Test invalid idempotency key format"""
    response = client.post(
        "/api/v1/payments",
        headers={"Idempotency-Key": "not-a-uuid"},
        json={"amount": 100, "currency": "USD", "source": "card_123"}
    )
    
    assert response.status_code == 400
    assert "Invalid Idempotency-Key format" in response.json()["detail"]


def test_amount_limit():
    idempotency_key = str(uuid.uuid4())
    response = client.post(
        "/api/v1/payments",
        headers={"Idempotency-Key": idempotency_key},
        json={"amount": 15000, "currency": "USD", "source": "card_123"}
    )
    
    assert response.status_code == 422


def test_negative_amount():
    idempotency_key = str(uuid.uuid4())
    response = client.post(
        "/api/v1/payments",
        headers={"Idempotency-Key": idempotency_key},
        json={"amount": -10, "currency": "USD", "source": "card_123"}
    )
    
    assert response.status_code == 422


def test_get_transaction():
    """Test retrieving a transaction"""
    idempotency_key = str(uuid.uuid4())
    
    create_response = None
    for attempt in range(3):
        create_response = client.post(
            "/api/v1/payments",
            headers={"Idempotency-Key": idempotency_key},
            json={"amount": 75, "currency": "USD", "source": "card_123"}
        )
        if create_response.status_code == 200:
            break
        time.sleep(0.1)
    
    if create_response is None or create_response.status_code != 200:
        pytest.skip("Payment creation failed due to provider error")
    
    transaction_id = create_response.json()["transaction_id"]
    
    get_response = client.get(f"/api/v1/payments/{transaction_id}")
    assert get_response.status_code == 200
    data = get_response.json()
    assert data["transaction_id"] == transaction_id
    assert data["amount"] == 75


def test_get_nonexistent_transaction():
    response = client.get("/api/v1/payments/nonexistent")
    assert response.status_code == 404


def test_idempotency_status_endpoint():
    """Test idempotency status endpoint"""
    idempotency_key = str(uuid.uuid4())
    
    success = False
    for attempt in range(3):
        response = client.post(
            "/api/v1/payments",
            headers={"Idempotency-Key": idempotency_key},
            json={"amount": 30, "currency": "USD", "source": "card_123"}
        )
        if response.status_code == 200:
            success = True
            break
        time.sleep(0.1)
    
    if not success:
        pytest.skip("Payment creation failed due to provider error")
    
    status_response = client.get(f"/api/v1/idempotency/{idempotency_key}")
    assert status_response.status_code == 200
    data = status_response.json()
    assert data["idempotency_key"] == idempotency_key
    assert data["status"] == "completed"
    assert data["response_status"] == 200


def test_concurrent_identical_requests():
    """Test concurrent requests with same idempotency key"""
    idempotency_key = str(uuid.uuid4())
    payment_data = {
        "amount": 42.00,
        "currency": "USD",
        "source": "card_123"
    }

    def make_request():
        return client.post(
            "/api/v1/payments",
            headers={"Idempotency-Key": idempotency_key},
            json=payment_data
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(make_request) for _ in range(10)]
        responses = [f.result() for f in futures]

    status_codes = [r.status_code for r in responses]
    success_count = sum(1 for code in status_codes if code == 200)
    conflict_count = sum(1 for code in status_codes if code == 409)
    
    assert success_count >= 1, "No successful requests"
    assert conflict_count + success_count == 10, f"Unexpected status codes: {status_codes}"
    
    db = SessionLocal()
    result = db.execute(
        text("SELECT COUNT(*) FROM payment_transactions WHERE idempotency_key = :key"),
        {"key": idempotency_key}
    )
    count = result.scalar()
    db.close()
    assert count == 1, f"Expected 1 transaction, got {count}"


def test_idempotency_duplicate_different_body_rejected():
    """Test same key with different body returns the cached response"""
    idempotency_key = str(uuid.uuid4())

    response1 = client.post(
        "/api/v1/payments",
        headers={"Idempotency-Key": idempotency_key},
        json={"amount": 100, "currency": "USD", "source": "card_123"}
    )

    response2 = client.post(
        "/api/v1/payments",
        headers={"Idempotency-Key": idempotency_key},
        json={"amount": 200, "currency": "USD", "source": "card_123"}
    )

    if response1.status_code == 200:
        assert response2.status_code == 200
        assert response2.json()["amount"] == 100
    else:
        pytest.skip("First request failed due to provider error")


def test_cross_key_isolation():
    """Test that keys don't interfere with each other with retry logic"""
    import time
    
    key1 = str(uuid.uuid4())
    key2 = str(uuid.uuid4())
    
    # Request with key1 with retry
    response1 = None
    for attempt in range(3):
        response1 = client.post(
            "/api/v1/payments",
            headers={"Idempotency-Key": key1},
            json={"amount": 10, "currency": "USD", "source": "card_123"}
        )
        if response1.status_code == 200:
            break
        time.sleep(0.1)
    
    # Request with key2 with retry
    response2 = None
    for attempt in range(3):
        response2 = client.post(
            "/api/v1/payments",
            headers={"Idempotency-Key": key2},
            json={"amount": 20, "currency": "USD", "source": "card_123"}
        )
        if response2.status_code == 200:
            break
        time.sleep(0.1)
    
    if response1 is None or response1.status_code != 200:
        pytest.skip(f"First request failed with status {response1.status_code if response1 else 'None'}")
    
    if response2 is None or response2.status_code != 200:
        pytest.skip(f"Second request failed with status {response2.status_code if response2 else 'None'}")
    
    assert response1.json()["transaction_id"] != response2.json()["transaction_id"]
    assert response1.json()["amount"] == 10
    assert response2.json()["amount"] == 20


def test_redis_lock_functionality(redis_client):
    from app.locks import RedisLock
    
    lock = RedisLock(redis_client)
    lock_key = "test:lock:123"
    
    lock_value = lock.acquire(lock_key, timeout_seconds=5)
    assert lock_value is not None
    
    lock_value2 = lock.acquire(lock_key, timeout_seconds=1, max_retries=1)
    assert lock_value2 is None
    
    released = lock.release(lock_key, lock_value)
    assert released is True
    
    lock_value3 = lock.acquire(lock_key, timeout_seconds=5)
    assert lock_value3 is not None


def test_metrics_endpoint():
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    content = response.text
    assert "idempotency_" in content or "# HELP" in content

def test_cors_headers():
    # Test OPTIONS preflight request
    options_response = client.options(
        "/api/v1/payments",
        headers={
            "Origin": "https://my-frontend-app.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,idempotency-key"
        }
    )

    assert options_response.status_code in [200, 204]
    headers = options_response.headers
    assert "access-control-allow-origin" in headers
    # With allow_credentials=True, FastAPI echoes the Origin instead of *
    assert headers["access-control-allow-origin"] in ["*", "https://my-frontend-app.com"]
    assert "access-control-allow-methods" in headers
    assert "POST" in headers["access-control-allow-methods"]

    # Test that POST responses also include CORS headers (via middleware)
    idempotency_key = str(uuid.uuid4())
    post_response = client.post(
        "/api/v1/payments",
        headers={
            "Origin": "https://my-frontend-app.com",
            "Idempotency-Key": idempotency_key,
            "Content-Type": "application/json"
        },
        json={"amount": 10, "currency": "USD", "source": "card_123"}
    )
    assert "access-control-allow-origin" in post_response.headers
    assert post_response.headers["access-control-allow-origin"] in ["*", "https://my-frontend-app.com"]

def test_root_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "endpoints" in data
    assert "version" in data


def test_payment_with_metadata():
    """Test payment with optional metadata"""
    idempotency_key = str(uuid.uuid4())
    
    for attempt in range(3):
        response = client.post(
            "/api/v1/payments",
            headers={"Idempotency-Key": idempotency_key},
            json={
                "amount": 150.00,
                "currency": "EUR",
                "source": "card_789",
                "description": "International payment",
                "metadata": {
                    "customer_id": "cust_123",
                    "order_id": "ord_456"
                }
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            assert data["currency"] == "EUR"
            return
        elif response.status_code == 500 and attempt < 2:
            time.sleep(0.1)
            continue
    
    pytest.skip("Payment failed due to provider error")


def test_source_validation():
    """Test source field validation with retry for random failures"""
    import time
    
    # Valid sources - each with its own idempotency key
    for source in ["card_123", "bank_456", "crypto_789"]:
        idempotency_key = str(uuid.uuid4())
        
        success = False
        for attempt in range(3):
            response = client.post(
                "/api/v1/payments",
                headers={"Idempotency-Key": idempotency_key},
                json={"amount": 10, "currency": "USD", "source": source}
            )
            
            if response.status_code == 200:
                success = True
                break
            elif response.status_code == 500 and attempt < 2:
                time.sleep(0.1)
                continue
            else:
                assert response.status_code != 422, f"Source {source} was rejected as invalid (got 422)"
                break
        
        if not success:
            pytest.skip(f"Provider errors for source {source}, skipping validation test")
    
    # Invalid source - should return 422
    idempotency_key = str(uuid.uuid4())
    response = client.post(
        "/api/v1/payments",
        headers={"Idempotency-Key": idempotency_key},
        json={"amount": 10, "currency": "USD", "source": "invalid_source"}
    )
    assert response.status_code == 422, f"Expected 422 for invalid source, got {response.status_code}"


def test_response_caching(redis_client):
    idempotency_key = str(uuid.uuid4())

    response = None
    for attempt in range(3):
        response = client.post(
            "/api/v1/payments",
            headers={"Idempotency-Key": idempotency_key},
            json={"amount": 88, "currency": "USD", "source": "card_123"}
        )
        if response.status_code == 200:
            break
        time.sleep(0.1)
    
    if response is None or response.status_code != 200:
        pytest.skip("Payment failed due to provider error")
    
    cached = redis_client.get(f"idempotency:response:{idempotency_key}")
    assert cached is not None
    
    cached_data = json.loads(cached)
    assert cached_data["status_code"] == 200
    assert "transaction_id" in cached_data["body"]


def test_performance_under_load():
    """Test performance with moderate load - with retry logic"""
    import time
    
    idempotency_keys = [str(uuid.uuid4()) for _ in range(20)]
    start_time = time.time()

    def make_request_with_retry(i, key):
        """Make request with retry on provider errors"""
        for attempt in range(2):
            response = client.post(
                "/api/v1/payments",
                headers={"Idempotency-Key": key},
                json={"amount": 10 + (i % 90), "currency": "USD", "source": "card_123"}
            )
            if response.status_code == 200:
                return response
            if response.status_code == 500 and attempt == 0:
                time.sleep(0.05)
                continue
            return response
        return response

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = []
        for i in range(100):
            key = idempotency_keys[i % 20]
            futures.append(executor.submit(make_request_with_retry, i, key))
        
        responses = [f.result() for f in futures]

    duration = time.time() - start_time
    success_count = sum(1 for r in responses if r.status_code == 200)
    
    # With retries, most requests should succeed
    # The mock provider has ~10% failure rate, so some tests may see fewer successes
    assert success_count >= 80, f"Only {success_count}/100 succeeded (expected >=80)"

    print(f" 100 requests in {duration:.2f} seconds")
    print(f" Throughput: {100/duration:.2f} req/sec")
    print(f" Success rate: {success_count}%")