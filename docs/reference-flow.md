# Observed branches

Nine user-supplied screenshots were reviewed on 2026-09-24. These are mobile
Chrome/Brave screenshots, not a recording of one deterministic flow. The source
images and their account identifiers, QR codes, and verification URLs are not
part of the repository.

| Branch | Visible evidence | Worker behavior |
| --- | --- | --- |
| Organization selection | “Bergabung dengan tim Anda”, “Buat organisasi baru” | Recognize separately from login; use the explicit new-organization action, never choose a suggested organization by position. |
| Bank OTP | “Kode Otentikasi”, “ID Check” / Mastercard, merchant Anthropic | Need attention; admin enters the bank code in the official frame. |
| Bank app | “Konfirmasi transaksi-mu di aplikasi Raya”, merchant Anthropic | Need attention; admin approves in the bank's mobile app. |
| Persona device handoff | “Continue on another device”, QR, “Send Email” | Need attention; continue on the user's phone. The VPS browser does not have the phone's camera. |
| Persona camera | “Verify with your camera” | Need attention; identity verification remains on the official provider's flow. |
| Persona document country | “What country is your government ID from?” | Need attention; billing country is not assumed to be document country. |
| Expired Persona link | “Tautan kedaluwarsa” | Request a fresh official link; the screenshot provides no actual resend control on this screen. |
| Persona completion | “Selamat, Anda sudah selesai!” | Identity step finished; no inference that credits were purchased or that the account is active. |
| Suspension notification | Subject “Your account has been suspended” | Candidate suspension evidence; verify actual sender, receiving account, and received time. Display name alone is insufficient. |

Both bank screenshots show USD 0.00. Treat this as a card-authentication step,
not proof of a completed credit purchase. Another screenshot shows a Mastercard;
the current admin form accepts Visa only, as originally requested.

The “Send Email” action in the screenshot belongs to device handoff. Its presence
does not prove it creates a new inquiry or refreshes an expired link. Retry must
inspect the current state and confirm the provider's result before reporting that
a fresh link was sent. No retry on a generic button labeled “Kirim Ulang”: that
could resend a bank OTP rather than a Persona verification link.

## Current operator actions

Retry now means refreshing the selected worker's current page, not resending an
email or creating a new Persona inquiry. When the browser has closed, restore its
encrypted cookies and last allowed-provider URL. If the URL is expired, refresh
may still show expiry; request operator attention. Never report a new link sent.

Cek opens `platform.claude.com/dashboard` and, with an explicit amount/charge
limit, continues toward a purchase on the card already linked to that account.
Logout is a session/authentication result; only explicit suspension evidence is
classified as suspension. Stored-card checkout requires no new card number/CVV.

## What is still missing for live verification

- Authenticated onboarding form fields and the actual checkout showing USD amount,
  tax/fees, final total, submit control, and positive purchase confirmation.
- Current DOM and URLs from an authorized browser session; screenshots establish
  text/state clues, not stable element selectors or embedded-frame origins.
- Expanded suspension-email header showing the sender address and mailbox/time.

Until those are verified, billing and email extraction capabilities
remain disabled. Recognition is locale-aware for the observed Indonesian/English
texts and independent of viewport coordinates. Unknown variants request attention.
