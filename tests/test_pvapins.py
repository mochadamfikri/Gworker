import json
import unittest
from unittest.mock import patch

from familylink.pvapins import PVAPinsClient, ProviderConfig, PVAPinsError
from familylink.worker import WorkerController


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return json.dumps(self.payload).encode()


class PVAPinsClientTests(unittest.TestCase):
    def test_account_and_balance(self):
        with patch("familylink.pvapins.urlopen", return_value=FakeResponse({"balance": 4.25, "id": "abc"})):
            client = PVAPinsClient("secret")
            self.assertEqual(client.balance(), 4.25)
            self.assertEqual(client.test_connection()["account_id"], "abc")

    def test_services_and_operators(self):
        responses = [
            {"services": [{"code": "go", "name": "Gmail"}]},
            {"operators": [{"operator": 2, "price": 0.15, "count": 3}]},
        ]
        with patch("familylink.pvapins.urlopen", side_effect=[FakeResponse(x) for x in responses]):
            client = PVAPinsClient("secret", ProviderConfig(country="IN", service="go"))
            self.assertEqual(client.services()[0]["code"], "go")
            self.assertEqual(client.operators()[0]["operator"], 2)

    def test_reserve_uses_idempotency_header(self):
        captured = {}
        def fake(req, timeout):
            captured["headers"] = dict(req.header_items())
            captured["body"] = json.loads(req.data.decode())
            return FakeResponse({"id": "1", "phoneNumber": "+100"})
        with patch("familylink.pvapins.urlopen", side_effect=fake):
            client = PVAPinsClient("secret")
            result = client.reserve("IN", "go", 2)
        self.assertEqual(result["id"], "1")
        self.assertIn("Idempotency-key", {k.title(): v for k, v in captured["headers"].items()})
        self.assertEqual(captured["body"]["operator"], 2)
        self.assertNotIn("idempotencyKey", captured["body"])

    def test_otp_polling(self):
        responses = [
            {"id": "1", "status": "active"},
            {"id": "1", "status": "completed", "otpCode": "123456"},
        ]
        with patch("familylink.pvapins.urlopen", side_effect=[FakeResponse(x) for x in responses]):
            with patch("familylink.pvapins.time.sleep"):
                result = PVAPinsClient("secret").wait_for_otp("1", timeout=2, interval=0)
        self.assertEqual(result["otpCode"], "123456")


if __name__ == "__main__":
    unittest.main()
