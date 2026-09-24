import pytest
from uuid import uuid4


@pytest.fixture
def payload():
    return {
        "request_id": str(uuid4()),
        "accounts": [{"email": f"test{i}@example.com", "password": "test:password"} for i in range(4)],
        "concurrency": 2,
        "card": {"holder": "Test Person", "number": "4242424242424242", "expiry": "12/39", "cvv": "123"},
        "identity": {"full_name": "Test Person", "organization": "Test Org", "address": "Test Street 1",
                     "city": "Test City", "region": "Test Region", "country": "ID", "postal_code": "12345"},
        "amount_usd": "5.00", "total_limit_usd": "20.00",
    }
