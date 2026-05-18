#!/usr/bin/env python
"""
Generate realistic traffic for monitoring
Run: python generate_traffic.py
"""

import requests
import uuid
import time
import random
from datetime import datetime

BASE_URL = "http://localhost:8000"

def generate_payment():
    """Generate a single payment request"""
    idempotency_key = str(uuid.uuid4())
    
    # Random amount between $10 and $500
    amount = random.randint(10, 500)
    
    # Random source
    sources = ["card_123", "bank_456", "crypto_789"]
    source = random.choice(sources)
    
    try:
        response = requests.post(
            f"{BASE_URL}/api/v1/payments",
            headers={
                "Idempotency-Key": idempotency_key,
                "Content-Type": "application/json"
            },
            json={
                "amount": amount,
                "currency": "USD",
                "source": source,
                "description": f"Test payment ${amount}"
            },
            timeout=5
        )
        
        status = "✅ SUCCESS" if response.status_code == 200 else f"❌ FAILED ({response.status_code})"
        print(f"{datetime.now().strftime('%H:%M:%S')} - {status} - Amount: ${amount}")
        
    except Exception as e:
        print(f"{datetime.now().strftime('%H:%M:%S')} - ❌ ERROR: {e}")

def generate_duplicate_requests():
    """Test idempotency by sending duplicate requests"""
    idempotency_key = str(uuid.uuid4())
    amount = 100
    
    print(f"\n🔄 Testing idempotency with key: {idempotency_key[:8]}...")
    
    # First request
    response1 = requests.post(
        f"{BASE_URL}/api/v1/payments",
        headers={"Idempotency-Key": idempotency_key},
        json={"amount": amount, "currency": "USD", "source": "card_123"}
    )
    
    # Second request (duplicate)
    response2 = requests.post(
        f"{BASE_URL}/api/v1/payments",
        headers={"Idempotency-Key": idempotency_key},
        json={"amount": amount, "currency": "USD", "source": "card_123"}
    )
    
    if response1.status_code == 200 and response2.status_code == 200:
        txn1 = response1.json().get("transaction_id")
        txn2 = response2.json().get("transaction_id")
        
        if txn1 == txn2:
            print(f"✅ Idempotency working! Same transaction: {txn1[:8]}...")
        else:
            print(f"❌ Idempotency failed! Different transactions")
    else:
        print(f"❌ Request failed: {response1.status_code}, {response2.status_code}")

def generate_invalid_keys():
    """Test invalid idempotency keys"""
    invalid_keys = ["not-a-uuid", "123", "abc-123", "invalid-format"]
    
    for key in invalid_keys:
        response = requests.post(
            f"{BASE_URL}/api/v1/payments",
            headers={"Idempotency-Key": key},
            json={"amount": 50, "currency": "USD", "source": "card_123"}
        )
        
        if response.status_code == 400:
            print(f"✅ Invalid key '{key}' correctly rejected (400)")
        else:
            print(f"⚠️ Invalid key '{key}' got {response.status_code}")

def main():
    print("=" * 60)
    print("📊 Generating Traffic for Monitoring")
    print("=" * 60)
    
    # Test 1: Invalid keys
    print("\n🔑 Testing invalid idempotency keys...")
    generate_invalid_keys()
    
    # Test 2: Idempotency test
    print("\n🔄 Testing idempotency...")
    generate_duplicate_requests()
    
    # Test 3: Generate 50 random payments
    print("\n💰 Generating 50 random payments...")
    for i in range(50):
        generate_payment()
        time.sleep(0.2)  # 5 requests per second
    
    # Test 4: Concurrent duplicates (idempotency stress test)
    print("\n⚡ Testing concurrent identical requests...")
    idempotency_key = str(uuid.uuid4())
    
    import concurrent.futures
    
    def send_duplicate():
        return requests.post(
            f"{BASE_URL}/api/v1/payments",
            headers={"Idempotency-Key": idempotency_key},
            json={"amount": 75, "currency": "USD", "source": "card_123"}
        )
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(send_duplicate) for _ in range(10)]
        responses = [f.result() for f in futures]
    
    status_codes = [r.status_code for r in responses]
    success_count = sum(1 for code in status_codes if code == 200)
    
    print(f"✅ {success_count}/10 concurrent requests succeeded")
    
    print("\n" + "=" * 60)
    print("✅ Traffic generation complete!")
    print("📊 Check your metrics at:")
    print("   - Prometheus: http://localhost:9090")
    print("   - Grafana: http://localhost:3000 (admin/admin)")
    print("   - Metrics endpoint: http://localhost:8000/metrics")
    print("=" * 60)

if __name__ == "__main__":
    main()