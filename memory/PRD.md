# PRD — Google Family Assistant Worker (Termux CLI)

## Original Problem Statement
Termux command-line worker yang menyiapkan & melacak aksi Family Link yang terotorisasi, human-in-the-loop. Tidak pernah melakukan login Google, OTP, CAPTCHA, atau pembuatan akun — semua di UI resmi Google. Tanpa SMS rental, rotasi nomor, atau bypass rate limit. Consent wali wajib per anak.

## User Choices
- Python Typer/Rich CLI + SQLite (tanpa web UI)
- Human-in-the-loop handoff ke URL resmi Google
- Penyimpanan terenkripsi (Fernet, key perms 600), tanpa password/cookie/token
- Suite pytest untuk edge cases

## Architecture
- `/app/familylink/` — package CLI standalone (bukan template FastAPI/React; /app/backend & /app/frontend tidak dipakai)
  - `cli.py` — Typer commands: init, family-head login, job create, jobs, confirm, resume, cancel, audit, monitor, simulate-failure
  - `service.py` — state machine (draft → awaiting_human_action → completed/failed/cancelled/in_review)
  - `validation.py` — tanggal lahir (tanpa modifikasi diam-diam), duplikat, kapasitas keluarga, batas pending
  - `scheduler.py` — bounded retry: backoff eksponensial hanya untuk kegagalan transien; permanen = tidak pernah retry
  - `workflow.py` — adapter handoff UI resmi Google (pengganti jio/hunt.py & jio/auth.py)
  - `storage/store.py` — SQLite: family_heads, jobs, consent_records, state_transitions, audit_events
  - `crypto.py` — Fernet SecretBox, key file 600
  - `redact.py` — masking email/nama, scrub OTP/secret dari audit
  - `monitor.py` — Rich live dashboard
- Config via env `FAMILYLINK_*` (HOME, MAX_FAMILY_MEMBERS=6, MAX_PENDING_JOBS=5, MAX_ATTEMPTS=5, backoff, time window)
- Idempotency key = sha256(family_head|child|birth_date|operation) → restart Termux tidak menduplikasi job

## Implemented (2026-06)
- Semua command CLI + confirmation screen + handoff resmi Google
- 6 hasil job: created, linked, pending_human_action, rejected, cancelled, unknown_requires_review
- Audit teredaksi + state transitions per job
- Kebijakan edge-case: rejected=permanen tanpa retry, transien=backoff terbatas lalu human review, resume wajib review manusia, pemulihan sesi terputus dari DB
- 29 pytest tests (semua lulus) + smoke test CLI (testing agent: 100%)
- `pip install -e .` → perintah `familylink`; juga `python -m familylink`

## Backlog / Next
- P1: Notifikasi pengingat wali (provider resmi, mis. email) — opsional sesuai pilihan user
- P2: Export audit ke JSON/CSV
- P2: Multi-household commands (list/switch family heads)
- P2: `familylink doctor` — cek perms/integritas DB
