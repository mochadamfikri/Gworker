import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import time

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError

from .browser import BrowserDriver
from .engine import Engine
from .models import BatchInput, Control, Login, SavedAddress, SavedCardTopup, Status
from .store import Store
from .sessions import SessionVault

STATIC = Path(__file__).parent / "static"


@dataclass
class Settings:
    password_hash: str
    origin: str = "http://127.0.0.1:8090"
    database: str = "var/antwork.db"
    max_concurrency: int = 5
    live_enabled: bool = False
    workers_enabled: bool = False
    session_seconds: int = 3600

    @classmethod
    def env(cls):
        return cls(
            password_hash=os.environ.get("ANTWORK_PASSWORD_HASH", ""),
            origin=os.environ.get("ANTWORK_ORIGIN", "http://127.0.0.1:8090").rstrip("/"),
            database=os.environ.get("ANTWORK_DATABASE", "var/antwork.db"),
            max_concurrency=int(os.environ.get("ANTWORK_MAX_CONCURRENCY", "5")),
            live_enabled=os.environ.get("ANTWORK_LIVE_ENABLED") == "1",
            workers_enabled=os.environ.get("ANTWORK_WORKERS_ENABLED") == "1",
        )


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"{salt}:{digest}"


def check_password(password, encoded):
    try:
        salt, _ = encoded.split(":")
        return hmac.compare_digest(hash_password(password, salt), encoded)
    except (ValueError, TypeError):
        return False


def create_app(settings=None, driver_factory=BrowserDriver):
    settings = settings or Settings.env()
    live_enabled = settings.live_enabled and getattr(driver_factory, "billing_verified", False)
    sessions = {}
    attempts = []

    @asynccontextmanager
    async def lifespan(app):
        if not settings.password_hash:
            raise RuntimeError("Set ANTWORK_PASSWORD_HASH before starting AntWork")
        store = Store(settings.database)
        vault = SessionVault(Path(settings.database).parent / "sessions")
        app.state.engine = Engine(store, driver_factory, settings.max_concurrency, vault=vault)
        try:
            yield
        finally:
            await app.state.engine.close()
            store.close()
            sessions.clear()

    app = FastAPI(title="AntWork", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def authorized(token):
        timestamp = time.monotonic()
        for key in list(sessions):
            if sessions[key] <= timestamp:
                del sessions[key]
        return bool(token and token in sessions)

    @app.middleware("http")
    async def protect(request: Request, call_next):
        path = request.url.path
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("origin") != settings.origin:
                return JSONResponse({"detail": "Origin tidak diizinkan"}, status_code=403)
            length = request.headers.get("content-length", "0")
            if not length.isdecimal() or int(length) > 256_000:
                return JSONResponse({"detail": "Payload terlalu besar"}, status_code=413)
        if path.startswith("/api/") and path not in {"/api/login", "/api/health"}:
            if not authorized(request.cookies.get("antwork_session")):
                return JSONResponse({"detail": "Login diperlukan"}, status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # FastAPI's default response includes submitted input; redact it entirely.
        return JSONResponse({"detail": "Data tidak valid. Periksa akun, Visa, BB/TT, CVV, dan concurrency."}, status_code=422)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.post("/api/login")
    async def login(body: Login, response: Response):
        timestamp = time.monotonic()
        attempts[:] = [t for t in attempts if t > timestamp - 60]
        if len(attempts) >= 5:
            raise HTTPException(429, "Terlalu banyak percobaan; tunggu satu menit")
        attempts.append(timestamp)
        if not await asyncio.to_thread(check_password, body.password.get_secret_value(), settings.password_hash):
            raise HTTPException(401, "Password tidak sesuai")
        token = secrets.token_urlsafe(32)
        sessions[token] = timestamp + settings.session_seconds
        response.set_cookie("antwork_session", token, httponly=True,
                            secure=settings.origin.startswith("https://"), samesite="strict",
                            max_age=settings.session_seconds)
        return {"ok": True}

    @app.post("/api/logout")
    async def logout(request: Request, response: Response):
        sessions.pop(request.cookies.get("antwork_session"), None)
        response.delete_cookie("antwork_session")
        return {"ok": True}

    @app.get("/api/state")
    async def state():
        engine = app.state.engine
        return {"jobs": engine.store.list(), "active_batch": engine.batch_id if engine.secrets else None,
                "concurrency": engine.concurrency, "max_concurrency": settings.max_concurrency,
                "live_enabled": live_enabled, "workers_enabled": settings.workers_enabled or live_enabled,
                "email_check_enabled": getattr(driver_factory, "email_verified", False),
                "account_check_enabled": True,
                "saved_card_topup_enabled": settings.live_enabled and getattr(driver_factory, "saved_card_verified", False),
                "refresh_enabled": True}

    @app.get("/api/address")
    async def address():
        return {"address": app.state.engine.store.get_address()}

    @app.put("/api/address")
    async def save_address(body: SavedAddress):
        app.state.engine.store.save_address(body)
        return {"ok": True}

    @app.post("/api/batches", status_code=201)
    async def batch(body: BatchInput):
        if not (settings.workers_enabled or live_enabled):
            raise HTTPException(409, "Worker belum diaktifkan")
        try:
            batch_id = app.state.engine.start(body)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"batch_id": batch_id}

    @app.post("/api/concurrency/{value}")
    async def concurrency(value: int):
        if not 1 <= value <= settings.max_concurrency:
            raise HTTPException(422, "Concurrency di luar batas")
        engine = app.state.engine
        engine.concurrency = value
        engine.wakeup.set()
        return {"concurrency": value}

    def get_job(job_id):
        job = app.state.engine.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Sesi worker tidak tersedia")
        return job

    @app.post("/api/jobs/{job_id}/actions/{action}", status_code=202)
    async def worker_action(job_id: str, action: str):
        if action == "check_email" and not getattr(driver_factory, "email_verified", False):
            raise HTTPException(409, "Pemeriksa email Anthropic belum diverifikasi")
        current = app.state.engine.jobs.get(job_id)
        if action == "refresh_session" and current and current.status == Status.attention:
            try:
                await app.state.engine.refresh_active(job_id)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            except Exception:
                raise HTTPException(409, "Halaman belum berhasil dimuat ulang; periksa browser worker") from None
            return {"job_id": job_id}
        try:
            action_id = app.state.engine.enqueue_action(job_id, action)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"job_id": action_id}

    @app.post("/api/jobs/{job_id}/topup", status_code=202)
    async def saved_card_topup(job_id: str, body: SavedCardTopup):
        if not settings.live_enabled or not getattr(driver_factory, "saved_card_verified", False):
            raise HTTPException(409, "Checkout kartu tertaut belum diverifikasi")
        try:
            action_id = app.state.engine.enqueue_action(job_id, "check_topup", payment=body)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"job_id": action_id}

    @app.post("/api/jobs/{job_id}/resume")
    async def resume(job_id: str):
        get_job(job_id)
        try:
            app.state.engine.continue_job(job_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/finish")
    async def finish(job_id: str):
        get_job(job_id)
        try:
            app.state.engine.finish_job(job_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel(job_id: str):
        get_job(job_id)
        await app.state.engine.cancel(job_id)
        return {"ok": True}

    @app.post("/api/batch/cancel")
    async def cancel_batch():
        engine = app.state.engine
        # Stop queued work before awaiting running task cancellation.
        engine.concurrency = 0
        for job_id in list(engine.jobs):
            await engine.cancel(job_id)
        return {"ok": True}

    @app.websocket("/api/jobs/{job_id}/browser")
    async def browser_socket(ws: WebSocket, job_id: str):
        token = ws.cookies.get("antwork_session")
        if ws.headers.get("origin") != settings.origin or not authorized(token):
            await ws.close(code=4403)
            return
        job = app.state.engine.jobs.get(job_id)
        if not job or job.status != Status.attention or not job.browser or job.controller:
            await ws.close(code=4409)
            return
        job.controller = True
        await ws.accept()

        async def frames():
            while authorized(token) and job.status == Status.attention:
                await ws.send_json({"tabs": job.browser.tabs()})
                await ws.send_bytes(await job.browser.frame())
                await asyncio.sleep(0.65)

        async def controls():
            while authorized(token) and job.status == Status.attention:
                raw = await ws.receive_text()
                if not authorized(token) or job.status != Status.attention:
                    return
                if len(raw) > 12000:
                    return
                event = Control.model_validate_json(raw)
                await job.browser.control(event)

        tasks = [asyncio.create_task(frames()), asyncio.create_task(controls())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except (WebSocketDisconnect, ValidationError):
            pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            job.controller = False
            try:
                await ws.close()
            except RuntimeError:
                pass

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/static/{filename}")
    async def static(filename: str):
        if filename not in {"app.js", "style.css"}:
            raise HTTPException(404)
        return FileResponse(STATIC / filename)

    return app
