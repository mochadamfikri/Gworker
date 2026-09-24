# AntWork

Private admin panel for isolated browser workers. Rebuild of Gworker.

## Current status

The panel, account import, concurrency scheduler, authentication, ephemeral batch
secrets, interruption handling, and interactive browser control are implemented.
**Live Claude onboarding and credit purchases are not ready for deployment.**
The authenticated forms, checkout total, KYC/3DS detection, and reliable purchase
confirmation still need to be mapped against an authorized test account. The
current browser adapter is a commissioning draft, not a working payment adapter.
`ANTWORK_LIVE_ENABLED` defaults to `0`; do not enable it to perform transactions.
Passing CI verifies local fixtures only, not Google login acceptance or live billing.

## Backup

- Original commit: `d48f329fc685e946a4be06e3688696986e29ba31`
- GitHub branch: `backup/pre-antwork-20260924`
- VPS Git bundle: `/opt/backups/Gworker-before-AntWork-20260924.bundle`
- SHA-256: `3c9308fe67f49454179299cb2aea39636e62292f9a842ae326dcf76fde42a816`

Restore with `git clone /opt/backups/Gworker-before-AntWork-20260924.bundle restored-gworker`.

## Development and tests

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e .
.venv/bin/python -m playwright install --with-deps chromium
.venv/bin/pytest -q
node --check antwork/static/app.js
```

`tests/test_browser.py` runs real Chromium against local fixtures only. Tests
cover concurrency, attention timeouts, cancellation, restart recovery, secret
redaction, login/Origin checks, CSV/TXT import, responsive layout, remote input,
and cookie isolation. No real accounts, card details, or payments belong in CI.

## Operator workflow

1. Log into the private panel.
2. Import TXT or CSV with **one `email:password` per line**; one quoted CSV column
   is also accepted. Only the first colon separates the email and password.
3. Enter billing identity, Visa details, USD credit amount per account, a total
   batch limit, and concurrency. Review the total and authorize the batch.
4. Each queued account gets an isolated browser. `Need attention` retains its
   slot, preserving the maximum number of browsers. Other active workers continue.
5. Open the worker browser for manual verification. Only one controller can
   connect. Closing the view leaves automation paused; click **Lanjutkan** after
   finishing. The worker must verify the result rather than trusting that click.
6. Raising concurrency launches queued workers. Lowering it allows current
   workers to finish before starting more. A failed payment is never auto-retried.

## Data and deployment

SQLite stores job IDs, email addresses, statuses, fixed status messages,
timestamps, submission IDs, and the billing address explicitly saved by the admin.
Names, organization identity, credentials, and card details are held in the active
batch's memory, never in database records or logs. Remote frames exist in memory
only, with no screenshot files, HAR, traces, or recordings. References are released
after batch completion/cancellation; this is not a guarantee of secure RAM erasure.
Restart marks unfinished jobs `interrupted`; credentials must be entered again.

Use the dedicated service account in `deploy/antwork.service`, one Uvicorn process,
HTTPS with an authenticated panel, a RAM-backed runtime directory, disabled core
dumps and swap for the service. Do not run real secrets in a development server
that writes browser temporary files to a persistent `/tmp`.

Never put secrets in GitHub, Actions variables/artifacts, screenshots, access logs,
or command-line arguments. Nginx must use `client_max_body_size 256k`,
`client_body_buffer_size 256k`, `proxy_request_buffering off`,
`proxy_buffering off`, `access_log off`, and forward WebSocket Upgrade headers.
Panel hostname: `idsework.duckdns.org` (DNS verified as `54.151.240.15`). TLS certificate provisioning remains pending. Do not restart or
replace existing services while preparing this project.

## Release gates

- GitHub Actions green on the exact candidate commit.
- Verified authenticated onboarding and billing adapter, including amount/currency,
  final total, KYC/3DS attention, and an explicit successful transaction result.
- Controlled end-to-end run on an authorized test account with the approved amount.
- Correct panel hostname, TLS, admin password provisioning, and service limits.

The final service directory is `/opt/AntWork`. The current checkout is development
source only; no AntWork service has been installed or deployed.
