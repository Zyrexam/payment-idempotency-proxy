# steady_load.py — run this for clean Grafana graphs
import requests, uuid, time

while True:
    key = str(uuid.uuid4())
    requests.post("http://localhost:8000/api/v1/payments",
        headers={"Idempotency-Key": key},
        json={"amount": 50.00, "currency": "USD", "source": "card_123"})
    
    # Send duplicate (generates cache hit)
    requests.post("http://localhost:8000/api/v1/payments",
        headers={"Idempotency-Key": key},
        json={"amount": 50.00, "currency": "USD", "source": "card_123"})
    
    time.sleep(0.5)  # steady pace — not a burst