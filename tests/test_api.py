import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    
    data = response.json()
    assert data["status"] == "healthy"
    assert "timestamp" in data
    assert data["service"] == "payment-idempotency-proxy"


def test_root_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    
    data = response.json()
    assert data["version"] == "1.0.0"
    assert "docs_url" in data
    assert data["docs_url"] == "/docs"


def test_metrics_endpoint():
    response = client.get("/metrics")
    assert response.status_code == 200
    assert len(response.text) > 50  # Ensure actual metric data is present


def test_metrics_content_type():
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "idempotency_" in response.text


def test_payment_schema_validation():
    from app.schemas import PaymentRequest
    
    valid_request = PaymentRequest(
        amount=99.99,
        currency="USD",
        source="card_123456",
        description="Test payment"
    )
    assert valid_request.amount == 99.99
    assert valid_request.currency == "USD"

    with pytest.raises(Exception):
        PaymentRequest(amount=-10, currency="USD", source="card_123")

    with pytest.raises(Exception):
        PaymentRequest(amount=100, currency="USD", source="invalid_source")