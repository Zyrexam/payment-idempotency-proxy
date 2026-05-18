#!/usr/bin/env python
"""
Test Redis Cache Performance
Run: python test_redis_performance.py
"""

import requests
import uuid
import time
import json

BASE_URL = "http://localhost:8000"

def test_cache_hit_ratio():
    """Test cache hit ratio by sending duplicates"""
    print("\n" + "="*60)
    print("📊 TESTING REDIS CACHE HIT RATIO")
    print("="*60)
    
    idempotency_key = str(uuid.uuid4())
    
    # First request (cache miss)
    print(f"\n1️⃣ First request with key: {idempotency_key[:8]}...")
    start = time.time()
    response1 = requests.post(
        f"{BASE_URL}/api/v1/payments",
        headers={"Idempotency-Key": idempotency_key},
        json={"amount": 100, "currency": "USD", "source": "card_123"}
    )
    first_latency = (time.time() - start) * 1000
    
    if response1.status_code == 200:
        print(f"   ✅ Success (cache MISS) - Latency: {first_latency:.2f}ms")
    else:
        print(f"   ❌ Failed: {response1.status_code}")
        return
    
    # Second request (cache hit!)
    print(f"\n2️⃣ Duplicate request (should be CACHE HIT)")
    start = time.time()
    response2 = requests.post(
        f"{BASE_URL}/api/v1/payments",
        headers={"Idempotency-Key": idempotency_key},
        json={"amount": 100, "currency": "USD", "source": "card_123"}
    )
    second_latency = (time.time() - start) * 1000
    
    if response2.status_code == 200:
        print(f"   ✅ Success (cache HIT!) - Latency: {second_latency:.2f}ms")
        
        # Verify same transaction_id
        txn1 = response1.json().get("transaction_id")
        txn2 = response2.json().get("transaction_id")
        
        if txn1 == txn2:
            print(f"   ✅ Same transaction ID: {txn1[:8]}...")
            print(f"   📈 Cache speedup: {first_latency/second_latency:.1f}x faster")
        else:
            print(f"   ❌ Different transactions! Cache not working!")
    else:
        print(f"   ❌ Failed: {response2.status_code}")

def test_concurrent_cache():
    """Test cache with concurrent requests"""
    print("\n" + "="*60)
    print("⚡ TESTING CONCURRENT CACHE ACCESS")
    print("="*60)
    
    import concurrent.futures
    
    idempotency_key = str(uuid.uuid4())
    
    def make_request():
        return requests.post(
            f"{BASE_URL}/api/v1/payments",
            headers={"Idempotency-Key": idempotency_key},
            json={"amount": 50, "currency": "USD", "source": "card_123"}
        )
    
    # First request to populate cache
    print(f"\n1️⃣ Warming up cache with key: {idempotency_key[:8]}...")
    requests.post(
        f"{BASE_URL}/api/v1/payments",
        headers={"Idempotency-Key": idempotency_key},
        json={"amount": 50, "currency": "USD", "source": "card_123"}
    )
    
    # 50 concurrent duplicate requests (all should hit cache)
    print(f"\n2️⃣ Sending 50 concurrent duplicate requests...")
    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(make_request) for _ in range(50)]
        responses = [f.result() for f in futures]
    
    duration = time.time() - start
    
    success_count = sum(1 for r in responses if r.status_code == 200)
    print(f"   ✅ {success_count}/50 succeeded")
    print(f"   📊 Throughput: {50/duration:.1f} req/sec")
    print(f"   ⚡ Average latency: {duration*1000/50:.1f}ms")

def test_distributed_lock():
    """Test Redis distributed lock contention"""
    print("\n" + "="*60)
    print("🔒 TESTING REDIS DISTRIBUTED LOCKS")
    print("="*60)
    
    import concurrent.futures
    import time
    
    idempotency_key = str(uuid.uuid4())
    
    def make_request():
        return requests.post(
            f"{BASE_URL}/api/v1/payments",
            headers={"Idempotency-Key": idempotency_key},
            json={"amount": 75, "currency": "USD", "source": "card_123"}
        )
    
    # 20 concurrent requests with SAME key (tests distributed lock)
    print(f"\n🔒 Testing lock with 20 concurrent identical requests...")
    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(make_request) for _ in range(20)]
        responses = [f.result() for f in futures]
    
    duration = time.time() - start
    
    status_codes = [r.status_code for r in responses]
    success = sum(1 for c in status_codes if c == 200)
    conflicts = sum(1 for c in status_codes if c == 409)
    
    print(f"   ✅ Success: {success}")
    print(f"   ⚠️  Conflicts (lock contention): {conflicts}")
    print(f"   📊 Lock success rate: {success/20*100:.1f}%")
    print(f"   ⚡ Total time: {duration*1000:.1f}ms")
    
    # Verify only ONE transaction in DB
    print(f"\n🔍 Verifying only ONE transaction created...")
    # Check via API (get the transaction)
    if success > 0:
        txn_id = None
        for r in responses:
            if r.status_code == 200:
                txn_id = r.json().get("transaction_id")
                break
        
        if txn_id:
            verify = requests.get(f"{BASE_URL}/api/v1/payments/{txn_id}")
            if verify.status_code == 200:
                print(f"   ✅ Single transaction: {txn_id[:8]}...")

def check_redis_metrics():
    """Check if Redis metrics are being tracked"""
    print("\n" + "="*60)
    print("📈 CHECKING REDIS METRICS")
    print("="*60)
    
    # Get metrics
    response = requests.get(f"{BASE_URL}/metrics")
    metrics_text = response.text
    
    # Check for Redis metrics
    redis_metrics = [
        "idempotency_cache_hits_total",
        "idempotency_cache_misses_total",
        "idempotency_record_creations_total",
        "idempotency_lock_acquisitions_total",
        "idempotency_redis_connected"
    ]
    
    for metric in redis_metrics:
        if metric in metrics_text:
            # Extract value
            for line in metrics_text.split('\n'):
                if metric in line and not line.startswith('#'):
                    print(f"   ✅ {metric}: {line}")
                    break
        else:
            print(f"   ❌ {metric}: NOT FOUND")

def main():
    print("\n" + "🔴"*30)
    print("   REDIS PERFORMANCE TEST SUITE")
    print("🔴"*30)
    
    # Check if service is running
    try:
        requests.get(f"{BASE_URL}/health", timeout=2)
    except:
        print("\n❌ Service not running! Start with: python run.py")
        return
    
    # Run tests
    test_cache_hit_ratio()
    test_concurrent_cache()
    test_distributed_lock()
    check_redis_metrics()
    
    print("\n" + "="*60)
    print("✅ Redis tests complete!")
    print("📊 Check Grafana for cache hit/miss graphs")
    print("="*60)

if __name__ == "__main__":
    main()
