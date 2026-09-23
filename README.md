# Google Family Link Assistant Worker (Termux)

CLI worker yang **hardened dan human-in-the-loop** untuk menyiapkan dan melacak
aksi Family Link yang *sah/terotorisasi*. Login, consent, OTP, CAPTCHA, dan
pembuatan akun **selalu** dilakukan di UI resmi Google — worker ini tidak pernah
melakukannya untuk Anda.

## Instalasi (Termux / Linux / macOS)

```bash
pkg install python          # Termux saja; butuh Python 3.11+
pip install -e .            # dari folder repo ini (memberi perintah `familylink`)
# atau tanpa install:
pip install -r requirements.txt
python -m familylink --help
```

## Alur pemakaian

```bash
familylink init                          # buat DB SQLite + key terenkripsi (perms 600)
familylink family-head login             # daftarkan identifier wali (email) — TANPA password
familylink job create                    # buat job: nama anak, tanggal lahir, consent wali
familylink jobs                          # daftar semua job
familylink confirm JOB_ID --result created   # catat hasil dari UI resmi Google
familylink resume JOB_ID                 # lanjutkan job (wajib review manusia)
familylink cancel JOB_ID                 # batalkan job
familylink audit JOB_ID                  # audit trail (sudah diredaksi)
familylink monitor                       # dashboard live (Rich)
```

Hasil yang boleh dicatat: `created`, `linked`, `pending`, `rejected`,
`cancelled`, `unknown`.

## Konfigurasi (opsional, via environment)

| Variabel | Default | Keterangan |
|---|---|---|
| `FAMILYLINK_HOME` | `~/.familylink` | Lokasi DB + key |
| `FAMILYLINK_MAX_FAMILY_MEMBERS` | `6` | Kapasitas anggota keluarga |
| `FAMILYLINK_MAX_PENDING_JOBS` | `5` | Batas job pending per family head |
| `FAMILYLINK_MAX_ATTEMPTS` | `5` | Maks retry untuk kegagalan transien |
| `FAMILYLINK_BACKOFF_BASE_SECONDS` | `2.0` | Basis exponential backoff |
| `FAMILYLINK_TOTAL_TIME_WINDOW_SECONDS` | `86400` | Jendela waktu total retry |

## Kebijakan keamanan

- Tidak pernah menyimpan password, cookie, token, atau OTP.
- Identifier family head dienkripsi (Fernet) dengan key file berizin `600`.
- Audit log diredaksi: tanpa OTP, kredensial, atau data pribadi lengkap.
- Penolakan permanen tidak pernah di-retry otomatis; kegagalan transien
  memakai exponential backoff terbatas, lalu eskalasi ke review manusia.
- Idempotency key mencegah job duplikat saat Termux restart.
- Tanggal lahir tidak pernah dimodifikasi diam-diam; kelayakan umur diputuskan
  Google di alur resmi, bukan di-hard-code di sini.

## Tes

```bash
pytest
```


## Modernized VPS mode

The modernization adds a server-side PVAPins REST v1 client and a small authenticated
admin panel. PVAPins documents REST v1 at `https://api.pvapins.com/api/v1`: account
balance, countries, services, operators, live numbers, orders and order polling are
available there. New orders use the `Idempotency-Key` header and OTP delivery is
polled; the provider documentation recommends backoff and honoring `Retry-After`.

Set these environment variables on the VPS:

```bash
export PVAPINS_API_KEY="YOUR_KEY"
export PVAPINS_COUNTRY="IN"
export PVAPINS_SERVICE="go"
# optional:
export PVAPINS_OPERATOR="2"

# required for the admin panel; use a long random value
export FAMILYLINK_ADMIN_TOKEN="CHANGE_ME"
export FAMILYLINK_ADMIN_HOST="127.0.0.1"
export FAMILYLINK_ADMIN_PORT="8787"
```

Start the panel:

```bash
familylink init
familylink admin
```

The panel can:
- start/stop the worker and show its live phase;
- test PVAPins connectivity and show balance;
- browse countries, services, operators and recent orders;
- select the country/service/operator used by the provider session;
- reserve a provider number and poll the provider order for an incoming OTP;
- mark a Google CAPTCHA/security challenge as a human-required stop.

**Important:** provider OTP retrieval is separate from Google verification. The worker
does not type parent-account OTPs, bypass CAPTCHA, bypass identity checks, or automate
security challenges. Those steps remain manual in Google's official UI. If a challenge
appears, the worker records a human-required state instead of attempting to defeat it.

For remote VPS access, keep the admin server bound to `127.0.0.1` and expose it through
an authenticated HTTPS reverse proxy rather than opening port 8787 directly to the
internet.
