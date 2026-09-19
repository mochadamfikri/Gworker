"""Google Family Link Assistant Worker.

A hardened, human-in-the-loop Termux CLI that prepares and tracks *authorized*
Family Link actions. It never performs Google login, consent, OTP, CAPTCHA,
or account creation on the user's behalf: those always happen in Google's
official UI. This worker only orchestrates, validates, and records results.
"""

__version__ = "1.0.0"
