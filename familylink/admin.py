"""VPS-friendly admin panel for Gworker.

Default bind is 127.0.0.1. Use an HTTPS reverse proxy for remote access.
Authentication is an environment token and is never stored in SQLite.
"""
from __future__ import annotations
import html, json, os, secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from .pvapins import PVAPinsClient, PVAPinsError
from .worker import WorkerController

def _json(handler: BaseHTTPRequestHandler, code: int, payload: object) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)

def _page(worker: WorkerController, provider: PVAPinsClient | None) -> str:
    state = html.escape(json.dumps(worker.status(), ensure_ascii=False, indent=2))
    balance = "not configured"
    if provider:
        try:
            balance = "$" + format(provider.balance(), ".4f")
        except Exception:
            balance = "error"
    return """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gworker Admin</title>
<style>
body{font-family:system-ui;margin:20px;max-width:1100px}button,input{padding:9px;margin:4px}
.card{border:1px solid #ddd;border-radius:12px;padding:16px;margin:12px 0}
pre{white-space:pre-wrap;background:#f5f5f5;padding:12px;border-radius:8px}
</style></head><body>
<h1>Gworker Admin</h1>
<div class="card"><b>Worker status</b><pre id="status">""" + state + """</pre>
<button onclick="post('/api/worker/start')">Start worker</button>
<button onclick="post('/api/worker/stop')">Stop worker</button>
<button onclick="get('/api/worker/status')">Refresh</button></div>
<div class="card"><b>PVAPins balance:</b> """ + html.escape(balance) + """
<br><button onclick="get('/api/provider/test')">Test connection</button>
<button onclick="get('/api/provider/services')">Services</button>
<button onclick="get('/api/provider/countries')">Countries</button>
<button onclick="get('/api/provider/operators?country=IN&service=go')">Operators</button>
<button onclick="get('/api/provider/orders')">Orders</button>
</div>
<div class="card"><b>Reserve number</b><br>
<input id="country" value="IN" placeholder="Country ISO">
<input id="service" value="go" placeholder="Service code/name">
<input id="operator" type="number" placeholder="Operator">
<button onclick="setConfig()">Set provider</button><button onclick="reserve()">Reserve</button>
<p>Provider OTP is polled here only as provider data. Parent verification/Google OTP entry remains manual.</p>
</div>
<div class="card"><b>API output</b><pre id="out">Ready.</pre></div>
<script>
async function get(url){let r=await fetch(url);let t=await r.text();document.querySelector('#out').textContent=t;if(url.includes('/worker/status'))document.querySelector('#status').textContent=t}
async function post(url,body={}){let r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});let t=await r.text();document.querySelector('#out').textContent=t;get('/api/worker/status')}
async function setConfig(){let op=document.querySelector('#operator').value;await post('/api/provider/config',{country:document.querySelector('#country').value,service:document.querySelector('#service').value,operator:op?Number(op):null})}
async function reserve(){let op=document.querySelector('#operator').value;await post('/api/provider/reserve',{country:document.querySelector('#country').value,service:document.querySelector('#service').value,operator:op?Number(op):null})}
setInterval(()=>get('/api/worker/status'),5000);
</script></body></html>"""

def run_admin(worker: WorkerController, provider: PVAPinsClient | None,
              host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    token = os.environ.get("FAMILYLINK_ADMIN_TOKEN", "").strip()
    if not token:
        raise RuntimeError("FAMILYLINK_ADMIN_TOKEN is required.")
    host = host or os.environ.get("FAMILYLINK_ADMIN_HOST", "127.0.0.1")
    port = port or int(os.environ.get("FAMILYLINK_ADMIN_PORT", "8787"))

    class Handler(BaseHTTPRequestHandler):
        def _auth(self) -> bool:
            supplied = self.headers.get("X-Admin-Token", "")
            query_token = parse_qs(urlparse(self.path).query).get("token", [""])[0]
            return secrets.compare_digest(supplied or query_token, token)

        def do_GET(self) -> None:
            if not self._auth():
                return _json(self, 401, {"error": "unauthorized"})
            path = urlparse(self.path).path
            try:
                if path == "/":
                    body = _page(worker, provider).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif path == "/api/worker/status":
                    _json(self, 200, worker.status())
                elif path == "/api/provider/test":
                    _json(self, 200, provider.test_connection() if provider else {"ok": False, "error": "not configured"})
                elif path == "/api/provider/services":
                    _json(self, 200, provider.services() if provider else [])
                elif path == "/api/provider/countries":
                    _json(self, 200, provider.countries() if provider else [])
                elif path == "/api/provider/operators":
                    q = parse_qs(urlparse(self.path).query)
                    _json(self, 200, provider.operators(q.get("country", [None])[0], q.get("service", [None])[0]) if provider else [])
                elif path == "/api/provider/orders":
                    _json(self, 200, provider.orders() if provider else [])
                else:
                    _json(self, 404, {"error": "not found"})
            except Exception as exc:
                _json(self, 502, {"error": str(exc)})

        def do_POST(self) -> None:
            if not self._auth():
                return _json(self, 401, {"error": "unauthorized"})
            length = int(self.headers.get("Content-Length", "0"))
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return _json(self, 400, {"error": "invalid JSON"})
            try:
                path = urlparse(self.path).path
                if path == "/api/worker/start":
                    return _json(self, 200, {"started": worker.start(), "status": worker.status()})
                if path == "/api/worker/stop":
                    worker.stop()
                    return _json(self, 200, {"stopped": True, "status": worker.status()})
                if path == "/api/provider/config":
                    if not provider:
                        raise PVAPinsError("PVAPins is not configured.")
                    if data.get("country"):
                        provider.config.country = str(data["country"])
                    if data.get("service"):
                        provider.config.service = str(data["service"])
                    provider.config.operator = int(data["operator"]) if data.get("operator") is not None else None
                    return _json(self, 200, {"country": provider.config.country, "service": provider.config.service, "operator": provider.config.operator})
                if path == "/api/provider/reserve":
                    if not provider:
                        raise PVAPinsError("PVAPins is not configured.")
                    result = worker.reserve_phone(data.get("country"), data.get("service"), data.get("operator"))
                    return _json(self, 200, result)
                if path == "/api/provider/otp":
                    if not provider:
                        raise PVAPinsError("PVAPins is not configured.")
                    result = worker.poll_provider_otp(str(data["order_id"]), int(data.get("timeout", 120)))
                    return _json(self, 200, result)
                if path == "/api/worker/challenge":
                    worker.mark_google_challenge(str(data["job_id"]), str(data.get("note", "")))
                    return _json(self, 200, worker.status())
                if path == "/api/worker/challenge/clear":
                    worker.clear_challenge()
                    return _json(self, 200, worker.status())
                return _json(self, 404, {"error": "not found"})
            except (KeyError, ValueError) as exc:
                return _json(self, 400, {"error": str(exc)})
            except Exception as exc:
                return _json(self, 502, {"error": str(exc)})

        def log_message(self, *_args) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)
