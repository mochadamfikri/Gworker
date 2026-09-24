"""Conservative page recognition based on the supplied mobile references.

Recognizing a challenge never counts as purchase success. Text samples contain
no account names, verification links, QR payloads, or card identifiers.
"""
from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr
from enum import StrEnum
import re
from urllib.parse import urlparse


class PageKind(StrEnum):
    unknown = "unknown"
    organization_picker = "organization_picker"
    bank_otp = "bank_otp"
    bank_app = "bank_app"
    persona_device = "persona_device"
    persona_camera = "persona_camera"
    persona_country = "persona_country"
    persona_expired = "persona_expired"
    persona_complete = "persona_complete"


ATTENTION_MESSAGES = {
    PageKind.bank_otp: "3DS meminta kode autentikasi dari bank; buka browser worker",
    PageKind.bank_app: "3DS meminta persetujuan di aplikasi bank pada HP",
    PageKind.persona_device: "Persona: lanjutkan di HP melalui QR atau Send Email",
    PageKind.persona_camera: "Persona meminta kamera dan identitas; lanjutkan verifikasi di HP",
    PageKind.persona_country: "Persona meminta negara penerbit dokumen identitas",
    PageKind.persona_expired: "Link Persona kedaluwarsa; perlu tautan baru dari alur resmi",
    PageKind.persona_complete: "Persona selesai; hasil pembelian saldo masih perlu diverifikasi",
    PageKind.unknown: "Halaman belum dikenali; buka browser worker untuk memeriksa",
}


def normalize(text):
    return re.sub(r"\s+", " ", text).strip().casefold()


def trusted_host(host, domain):
    return host == domain or host.endswith("." + domain)


def recognize(url, text, *, owner_url=None):
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return PageKind.unknown
    host = parsed.hostname or ""
    content = normalize(text)
    is_persona = trusted_host(host, "withpersona.com")
    if is_persona:
        if "tautan kedaluwarsa" in content or "link expired" in content or "link has expired" in content:
            return PageKind.persona_expired
        if ("selamat, anda sudah selesai" in content and "terima kasih telah memverifikasi" in content) or (
            "you're all done" in content and "verifying your identity" in content
        ):
            return PageKind.persona_complete
        if "continue on another device" in content and ("send email" in content or "qr code" in content):
            return PageKind.persona_device
        if "verify with your camera" in content:
            return PageKind.persona_camera
        if "what country is your government id from" in content:
            return PageKind.persona_country
    claude_hosts = {"platform.claude.com", "console.anthropic.com"}
    if host in claude_hosts:
        if ("bergabung dengan tim anda" in content and "buat organisasi baru" in content) or (
            "join your team" in content and "create" in content and "organization" in content
        ):
            return PageKind.organization_picker
    owner = urlparse(owner_url or url)
    # The bank's iframe host is not visible in the screenshots. Treat these as
    # operator attention signals only, never as permission to submit anything.
    if owner.scheme == "https" and owner.hostname in claude_hosts and "anthropic" in content:
        if "kode otentikasi" in content and ("id check" in content or "mastercard" in content):
            return PageKind.bank_otp
        if "konfirmasi transaksi" in content and "aplikasi raya" in content:
            return PageKind.bank_app
    return PageKind.unknown


def recognize_account(url, text):
    parsed = urlparse(url)
    content = normalize(text)
    if parsed.scheme != "https":
        return "unknown"
    if parsed.hostname == "accounts.google.com":
        return "needs_login"
    if parsed.hostname not in {"platform.claude.com", "console.anthropic.com"}:
        return "unknown"
    if any(message in content for message in (
        "your account has been suspended", "your account is suspended",
        "akun anda telah ditangguhkan", "akun anda ditangguhkan",
    )):
        return "suspended"
    if any(message in content for message in (
        "continue with google", "continue with email", "lanjutkan dengan google",
        "lanjutkan dengan email", "sign in to claude console",
    )):
        return "needs_login"
    # Positive authenticated UI evidence is required, not just the absence of a
    # login form or the address bar saying /dashboard.
    signed_in = any(marker in content for marker in ("log out", "sign out", "keluar"))
    dashboard = any(marker in content for marker in ("api keys", "kunci api", "billing", "penagihan"))
    if parsed.path.rstrip("/") == "/dashboard" and signed_in and dashboard:
        return "ready_for_topup"
    return "unknown"


@dataclass(frozen=True)
class MailEvidence:
    sender: str
    recipient: str
    subject: str
    received_at: datetime
    sender_authenticated: bool


def classify_mail(messages, *, account, since, checked_at, mailbox_account,
                  search_complete):
    """Classify evidence supplied by a trusted mailbox adapter, not by the UI.

The screenshot shows the exact suspension subject but only a display name, not
the sender's address. Live extraction/authentication still needs verification.
    """
    if not search_complete or mailbox_account.casefold() != account.casefold():
        return "needs_review"
    if since.tzinfo is None or checked_at.tzinfo is None or checked_at < since:
        return "needs_review"
    uncertain = False
    for message in messages:
        if message.received_at.tzinfo is None:
            uncertain = True
            continue
        if not since <= message.received_at <= checked_at:
            continue
        if normalize(message.subject) != "your account has been suspended":
            continue
        sender = parseaddr(message.sender)[1].casefold()
        domain = sender.rpartition("@")[2]
        recipient = parseaddr(message.recipient)[1].casefold()
        if (message.sender_authenticated and trusted_host(domain, "anthropic.com")
                and recipient == account.casefold()):
            return "suspended"
        uncertain = True
    return "needs_review" if uncertain else "no_suspend_detected"
