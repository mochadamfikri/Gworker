"""Family Link workflow adapter.

Replaces JioFarm's ``jio/hunt.py`` + ``jio/auth.py``. It supports ONLY:

1. Human handoff to Google's official Family Link UI (default), or
2. A documented, authorized integration if one is ever supplied.

There is currently **no** supported public Google Family Link account-creation
API. So the adapter never logs in, never submits OTP/CAPTCHA, never creates or
links accounts itself. It produces the official URL + guided manual steps and
then waits for the guardian to report the outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .config import Config
from .models import Operation


@dataclass
class Handoff:
    """A guided manual step to be performed by the guardian in Google's UI."""

    url: str
    title: str
    steps: List[str]
    reminder: str = (
        "All login, consent, OTP, CAPTCHA and identity verification happen in "
        "Google's official UI. This worker never sees or stores those values."
    )


def build_handoff(config: Config, operation: Operation) -> Handoff:
    base = config.official_base_url.rstrip("/")
    if operation == Operation.CREATE:
        return Handoff(
            url=f"{base}/",
            title="Create a supervised Google Account for your child",
            steps=[
                "Open the Google Family Link app, or visit the official Families page.",
                "Sign in as the Family Head (guardian) yourself.",
                "Choose 'Create account for your child' and complete every step Google shows.",
                "Provide guardian consent and complete any verification Google requires.",
                "Return here and record the actual result with `familylink confirm`.",
            ],
        )
    if operation == Operation.LINK:
        return Handoff(
            url=f"{base}/",
            title="Link an existing child account to your family",
            steps=[
                "Open Google Family Link and sign in as the Family Head.",
                "Send/accept the invitation to add the existing child account.",
                "Complete guardian consent and any verification in Google's UI.",
                "Return here and record the result with `familylink confirm`.",
            ],
        )
    if operation == Operation.MEMBER_STATUS:
        return Handoff(
            url=f"{base}/families",
            title="Check the member status for this child",
            steps=[
                "Open the official Families page and sign in as the Family Head.",
                "Review the child's membership / supervision status.",
                "Return here and record the observed status with `familylink confirm`.",
            ],
        )
    # CANCEL
    return Handoff(
        url=f"{base}/families",
        title="Cancel / remove the requested action",
        steps=[
            "If an invitation was sent, cancel it from the official Families page.",
            "If a member must be removed, do so in Google's UI as the Family Head.",
            "Return here and record the outcome with `familylink confirm`.",
        ],
    )
