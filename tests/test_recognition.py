from datetime import datetime, timedelta, timezone

import pytest

from antwork.recognition import MailEvidence, PageKind, classify_mail, recognize, recognize_account


@pytest.mark.parametrize("text,expected", [
    ("Continue on another device. Scan the QR code. Send Email", PageKind.persona_device),
    ("Verify with your camera. Continue", PageKind.persona_camera),
    ("What country is your government ID from? Indonesia Select", PageKind.persona_country),
    ("Tautan kedaluwarsa. Harap minta tautan baru untuk melanjutkan.", PageKind.persona_expired),
    ("Selamat, Anda sudah selesai! Terima kasih telah memverifikasi identitas Anda.", PageKind.persona_complete),
])
def test_persona_variants_are_independent_of_step_order(text, expected):
    assert recognize("https://inquiry.withpersona.com/verify", text) == expected
    assert recognize("https://withpersona.com.evil.example/verify", text) == PageKind.unknown


def test_organization_picker_and_zero_amount_3ds_are_not_purchase_success():
    url = "https://platform.claude.com/"
    assert recognize(url,"Bergabung dengan tim Anda. Buat organisasi baru") == PageKind.organization_picker
    assert recognize("https://bank.example/3ds", "ID Check. Kode Otentikasi Mastercard. ANTHROPIC USD 0,00", owner_url=url) == PageKind.bank_otp
    assert recognize("https://bank.example/3ds", "Konfirmasi transaksi-mu di aplikasi Raya. ANTHROPIC USD 0,00", owner_url=url) == PageKind.bank_app
    assert recognize("https://bank.example/3ds", "ID Check. Kode Otentikasi Mastercard. ANTHROPIC USD 0,00", owner_url="https://unrelated.example/") == PageKind.unknown
    assert recognize(url,"Payment method saved") == PageKind.unknown


def evidence(**overrides):
    values = dict(sender="Anthropic <notice@anthropic.com>", recipient="test@example.com",
                  subject="Your account has been suspended", received_at=datetime(2026,9,24,12,tzinfo=timezone.utc),
                  sender_authenticated=True)
    return MailEvidence(**(values | overrides))


def classify(messages, **overrides):
    args = dict(account="test@example.com",mailbox_account="test@example.com",search_complete=True,
                since=datetime(2026,9,24,tzinfo=timezone.utc),checked_at=datetime(2026,9,25,tzinfo=timezone.utc))
    return classify_mail(messages, **(args | overrides))


def test_suspension_requires_authenticated_sender_correct_mailbox_and_time():
    assert classify([evidence()]) == "suspended"
    assert classify([evidence(sender="Anthropic <notice@anthropic.com.evil.example>")]) == "needs_review"
    assert classify([evidence(sender_authenticated=False)]) == "needs_review"
    assert classify([evidence(recipient="other@example.com")]) == "needs_review"
    assert classify([evidence()],mailbox_account="other@example.com") == "needs_review"
    assert classify([evidence(received_at=datetime(2025,1,1,tzinfo=timezone.utc))]) == "no_suspend_detected"
    assert classify([],search_complete=False) == "needs_review"
    assert classify([]) == "no_suspend_detected"
    assert classify([evidence(subject="Your secure link to the Claude Console is here")]) == "no_suspend_detected"


def test_naive_or_inverted_timestamps_are_not_passes():
    assert classify([evidence(received_at=datetime(2026,9,24))]) == "needs_review"
    assert classify([],since=datetime(2026,9,24)) == "needs_review"
    assert classify([],since=datetime(2026,9,26,tzinfo=timezone.utc)) == "needs_review"


def test_dashboard_check_distinguishes_expired_login_and_suspension():
    assert recognize_account("https://platform.claude.com/", "Continue with Google") == "needs_login"
    assert recognize_account("https://accounts.google.com/signin", "Sign in") == "needs_login"
    assert recognize_account("https://platform.claude.com/dashboard", "Your account has been suspended") == "suspended"
    assert recognize_account("https://platform.claude.com/dashboard", "Billing API keys Log out") == "ready_for_topup"
    assert recognize_account("https://platform.claude.com/dashboard", "Loading") == "unknown"
    assert recognize_account("https://evil.example/dashboard", "Billing API keys Log out") == "unknown"
    assert recognize_account("https://platform.claude.com/", "Billing API keys Log out") == "unknown"
