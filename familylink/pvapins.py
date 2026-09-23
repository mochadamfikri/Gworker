"""PVAPins REST v1 client.

Server-side only. The API key is never written to logs or returned by the
admin API. This module supports provider inventory, balance, number
reservation, OTP polling and order lookup/release. It does not submit OTPs
to Google or bypass CAPTCHA/security challenges.
"""
from __future__ import annotations
import os, time, uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE_URL = "https://api.pvapins.com/api/v1"

class PVAPinsError(RuntimeError):
    pass

@dataclass
class ProviderConfig:
    country: str = "IN"
    service: str = "go"
    operator: int | None = None
    timeout: float = 20.0

class PVAPinsClient:
    def __init__(self, api_key: str, config: ProviderConfig | None = None):
        if not api_key or not api_key.strip():
            raise PVAPinsError("PVAPins API key is not configured.")
        self.api_key = api_key.strip()
        self.config = config or ProviderConfig()

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None,
                 body: dict[str, Any] | None = None, extra_headers: dict[str, str] | None = None) -> Any:
        query = ("?" + urlencode(params)) if params else ""
        url = BASE_URL.rstrip("/") + "/" + path.lstrip("/") + query
        headers = {"X-API-Key": self.api_key, "Accept": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        data = None
        if body is not None:
            import json
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.config.timeout) as resp:
                raw = resp.read().decode("utf-8")
                import json
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                import json
                payload = json.loads(raw)
            except Exception:
                payload = raw
            raise PVAPinsError(f"PVAPins HTTP {exc.code}: {payload}") from exc
        except (URLError, TimeoutError) as exc:
            raise PVAPinsError(f"PVAPins connection failed: {exc}") from exc
        except ValueError as exc:
            raise PVAPinsError("PVAPins returned invalid JSON.") from exc

    def account(self) -> dict[str, Any]:
        return dict(self._request("GET", "/account"))

    def balance(self) -> float:
        value = self.account().get("balance", 0)
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise PVAPinsError(f"Invalid balance response: {value!r}") from exc

    def countries(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/countries").get("countries", []))

    def services(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/services").get("services", []))

    def operators(self, country: str | None = None, service: str | None = None) -> list[dict[str, Any]]:
        cfg = self.config
        return list(self._request("GET", "/operators", {
            "country": country or cfg.country, "service": service or cfg.service
        }).get("operators", []))

    def numbers(self, country: str | None = None, service: str | None = None) -> list[dict[str, Any]]:
        cfg = self.config
        return list(self._request("GET", "/numbers", {
            "country": country or cfg.country, "service": service or cfg.service
        }).get("numbers", []))

    def reserve(self, country: str | None = None, service: str | None = None,
                operator: int | None = None) -> dict[str, Any]:
        cfg = self.config
        payload: dict[str, Any] = {
            "country": country or cfg.country, "service": service or cfg.service,
        }
        selected = operator if operator is not None else cfg.operator
        if selected is not None:
            payload["operator"] = selected
        return dict(self._request("POST", "/orders", body=payload,
                                  extra_headers={"Idempotency-Key": "gworker-" + uuid.uuid4().hex}))

    def order(self, order_id: str) -> dict[str, Any]:
        return dict(self._request("GET", f"/orders/{order_id}"))

    def orders(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/orders").get("orders", []))

    def wait_for_otp(self, order_id: str, timeout: int = 120,
                     interval: float = 4.0) -> dict[str, Any]:
        deadline = time.monotonic() + max(1, timeout)
        delay = max(2.0, interval)
        while time.monotonic() < deadline:
            data = self.order(order_id)
            status = str(data.get("status", "")).lower()
            if data.get("otpCode") or data.get("otp") or status in {"completed", "cancelled", "failed", "expired"}:
                return data
            time.sleep(delay)
            delay = min(delay * 1.4, 10.0)
        raise PVAPinsError(f"OTP timeout after {timeout}s for order {order_id}")

    def test_connection(self) -> dict[str, Any]:
        account = self.account()
        return {"ok": True, "balance": account.get("balance"), "account_id": account.get("id")}

def from_environment() -> PVAPinsClient | None:
    key = os.environ.get("PVAPINS_API_KEY", "").strip()
    if not key:
        return None
    operator = os.environ.get("PVAPINS_OPERATOR")
    cfg = ProviderConfig(
        country=os.environ.get("PVAPINS_COUNTRY", "IN"),
        service=os.environ.get("PVAPINS_SERVICE", "go"),
        operator=int(operator) if operator else None,
    )
    return PVAPinsClient(key, cfg)
