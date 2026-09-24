"""Live browser session. Unknown steps always request operator attention.

No stealth, CAPTCHA bypass, tracing, HAR, video, or persistent browser profile.
Authentication cookies are encrypted separately by the session vault.
Screenshots for remote control exist in memory only.
"""
import asyncio
import re
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from .recognition import ATTENTION_MESSAGES, PageKind, recognize


class BrowserDriver:
    billing_verified = False
    email_verified = False
    retry_verified = False

    def __init__(self):
        self.pw = self.browser = self.context = self.page = None
        self.lock = asyncio.Lock()

    async def open(self, saved=None):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=True)
        self.context = await self.browser.new_context(
            viewport={"width": 1280, "height": 800}, accept_downloads=False,
            service_workers="block",
            storage_state=saved,
        )
        self.context.set_default_timeout(10000)
        self.page = await self.context.new_page()

    async def maintain(self, job, saved, attention):
        if job.action != "open_session":
            raise RuntimeError("This live action has not yet been mapped and verified")
        await self.open(saved)
        await self.page.goto("https://platform.claude.com/", wait_until="domcontentloaded")
        await attention(job, "Sesi tersimpan dibuka; login ulang jika sesi sudah kedaluwarsa")
        return "session_opened"

    async def inspect_state(self):
        """Inspect page/iframe text without keeping DOM snapshots or recordings."""
        for page in reversed(self.context.pages):
            if page.is_closed():
                continue
            for frame in page.frames:
                try:
                    content = await frame.locator("body").inner_text(timeout=1500)
                except Exception:
                    continue
                kind = recognize(frame.url, content[:120_000], owner_url=page.url)
                if kind != PageKind.unknown:
                    self.page = page
                    return kind
        return PageKind.unknown

    async def attend_current_page(self, job, attention):
        kind = await self.inspect_state()
        await attention(job, ATTENTION_MESSAGES.get(kind, ATTENTION_MESSAGES[PageKind.unknown]))
        return kind

    async def handle_organization_picker(self):
        if await self.inspect_state() != PageKind.organization_picker:
            return False
        create = self.page.get_by_role("button", name=re.compile(
            r"^(Buat organisasi baru|Create (a )?new organization)$", re.I))
        if not await self.visible(create):
            return False
        await create.click()
        return True

    async def visible(self, locator):
        return await locator.count() == 1 and await locator.is_visible()

    async def run(self, job, account, batch, attention):
        await self.open()
        await self.page.goto("https://platform.claude.com/", wait_until="domcontentloaded")
        google = self.page.get_by_role("button", name="Continue with Google", exact=True)
        if await self.visible(google):
            await google.click()
        else:
            await attention(job, "Buka Continue with Google pada halaman resmi")

        email_sent = password_sent = False
        while True:
            pages = [p for p in self.context.pages if not p.is_closed()]
            if pages:
                self.page = pages[-1]
            host = urlparse(self.page.url).hostname
            if host == "accounts.google.com":
                email = self.page.locator('input[type="email"]')
                password = self.page.locator('input[type="password"]')
                if not email_sent and await self.visible(email):
                    await email.fill(account.email)
                    await self.page.locator("#identifierNext").click()
                    email_sent = True
                    await asyncio.sleep(1)
                    continue
                if not password_sent and await self.visible(password):
                    await password.fill(account.password.get_secret_value())
                    await self.page.locator("#passwordNext").click()
                    password_sent = True
                    await asyncio.sleep(1)
                    continue
            if host in {"platform.claude.com", "console.anthropic.com"} and password_sent:
                break
            await attention(job, "Login Google memerlukan tindakan; buka browser worker")

        # Billing/onboarding varies per organization. Until a verified adapter is
        # available, never guess a checkout button or claim payment completion.
        await self.handle_organization_picker()
        await self.attend_current_page(job, attention)
        await self.fill_card(batch.card)
        await self.attend_current_page(job, attention)
        # Explicitly stop rather than manufacture success from a generic page.
        raise RuntimeError("Live billing success detection is not configured")

    async def fill_card(self, card):
        """Only fill uniquely identified fields on the payment provider's origin."""
        matches = {
            "number": [], "expiry": [], "cvv": [],
        }
        selectors = {
            "number": 'input[autocomplete="cc-number"]',
            "expiry": 'input[autocomplete="cc-exp"]',
            "cvv": 'input[autocomplete="cc-csc"]',
        }
        for page in self.context.pages:
            for frame in page.frames:
                host = urlparse(frame.url).hostname or ""
                if host != "stripe.com" and not host.endswith(".stripe.com"):
                    continue
                for field, selector in selectors.items():
                    locator = frame.locator(selector)
                    if await self.visible(locator):
                        matches[field].append(locator)
        if not all(len(locators) == 1 for locators in matches.values()):
            raise RuntimeError("Payment fields not unambiguous")
        await matches["number"][0].fill(card.number.get_secret_value())
        await matches["expiry"][0].fill(card.expiry)
        await matches["cvv"][0].fill(card.cvv.get_secret_value())

    async def frame(self):
        async with self.lock:
            return await self.page.screenshot(type="jpeg", quality=65)

    async def control(self, event):
        async with self.lock:
            if event.kind == "click":
                await self.page.mouse.click(event.x, event.y)
            elif event.kind == "text":
                await self.page.keyboard.insert_text(event.text.get_secret_value())
            elif event.kind == "key":
                if not re.fullmatch(r"(?:Tab|Shift\+Tab|Enter|Backspace|Delete|Escape|Arrow(?:Up|Down|Left|Right)|Control\+a)", event.key):
                    raise ValueError("Unsupported key")
                await self.page.keyboard.press(event.key)
            elif event.kind == "scroll":
                await self.page.mouse.wheel(0, event.delta)
            elif event.kind == "tab":
                pages = [p for p in self.context.pages if not p.is_closed()]
                if event.tab >= len(pages):
                    raise ValueError("Tab no longer exists")
                self.page = pages[event.tab]

    def tabs(self):
        return [{"index": i, "host": urlparse(p.url).hostname or "halaman baru"}
                for i, p in enumerate(self.context.pages) if not p.is_closed()]

    async def close(self):
        try:
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
        finally:
            if self.pw:
                await self.pw.stop()
