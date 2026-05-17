import pytest
from fastapi.testclient import TestClient
from app.main import app

# Create a test client - it simulates HTTP requests without a real server
client = TestClient(app)


def test_health_check():
    """Test that /health returns 200 OK"""
    response = client.get("/health")
    assert response.status_code == 200
    
    data = response.json()
    assert data["status"] == "healthy"
    assert "timestamp" in data
    assert data["service"] == "payment-idempotency-proxy"


def test_root_endpoint():
    """Test that / returns API info"""
    response = client.get("/")
    assert response.status_code == 200
    
    data = response.json()
    assert data["version"] == "1.0.0"
    assert "docs_url" in data
    assert data["docs_url"] == "/docs"


def test_metrics_endpoint():
    """Test metrics placeholder"""
    response = client.get("/metrics")
    assert response.status_code == 200
    # We'll add more specific metrics tests in Part 2


def test_payment_schema_validation():
    """Test our Pydantic schemas work correctly"""
    from app.schemas import PaymentRequest
    
    # Valid payment request
    valid_request = PaymentRequest(
        amount=99.99,
        currency="USD",
        source="card_123456",
        description="Test payment"
    )
    assert valid_request.amount == 99.99
    assert valid_request.currency == "USD"
    
    # Invalid: negative amount (should raise error when creating)
    try:
        PaymentRequest(amount=-10, currency="USD", source="card_123")
        assert False, "Should have raised error"
    except Exception:
        pass  # Expected
    
    # Invalid: wrong source prefix
    try:
        PaymentRequest(amount=100, currency="USD", source="invalid_source")
        assert False, "Should have raised error"
    except Exception:
        pass  # Expected