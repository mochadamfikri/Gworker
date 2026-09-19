"""Redaction helpers.

Central place for the rule: never persist or display OTPs, passwords, tokens,
cookies, or full personal identifiers. Anything that flows into the audit log
or the dashboard passes through here first.
"""

from __future__ import annotations

import re

# Patterns that must never survive into an audit message.
_SECRET_HINT = re.compile(
    r"(otp|password|passwd|pwd|token|cookie|refresh[_-]?token|access[_-]?token|secret)",
    re.IGNORECASE,
)
_DIGIT_RUN = re.compile(r"\b\d{4,}\b")


def mask_email(identifier: str) -> str:
    """``alice@gmail.com`` -> ``a***@g***.com``. Non-emails are partially masked."""
    identifier = identifier.strip()
    if "@" in identifier:
        local, _, domain = identifier.partition("@")
        d_name, _, d_tld = domain.partition(".")
        ml = (local[0] + "***") if local else "***"
        md = (d_name[0] + "***") if d_name else "***"
        return f"{ml}@{md}.{d_tld}" if d_tld else f"{ml}@{md}"
    if len(identifier) <= 2:
        return "***"
    return identifier[0] + "***" + identifier[-1]


def mask_name(name: str) -> str:
    """Keep only the first initial of a child/guardian name for the audit log."""
    name = name.strip()
    if not name:
        return "***"
    return name[0].upper() + "***"


def redact(message: str) -> str:
    """Scrub anything that looks like a secret or a long digit run (e.g. OTP)."""
    if _SECRET_HINT.search(message):
        return "[redacted: sensitive value withheld]"
    return _DIGIT_RUN.sub("[redacted]", message)
