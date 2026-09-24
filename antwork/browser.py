"""Live browser session. Unknown steps always request operator attention.

No stealth, CAPTCHA bypass, tracing, HAR, video, or persistent browser profile.
Authentication cookies are encrypted separately by the session vault.
Screenshots for remote control exist in memory only.
"""
import asyncio
import re
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from .recognition import ATTENTION_MESSAGES, PageKind, recognize, recognize_account
from .sessions import safe_resume_url


class BrowserDriver:
    billing_verified = False
    email_verified = False
    saved_card_verified = False

    def __init__(self):
        self.pw = self.browser = self.context = self.page = None
        self.lock = asyncio.Lock()
        self.navigation_methods = {}
        self.resume_url = None

    async def open(self, saved=None):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=True)
        self.context = await self.browser.new_context(
            viewport={"width": 1280, "height": 800}, accept_downloads=False,
            service_workers="block",
            storage_state=saved,
        )
        self.context.set_default_timeout(10000)
        self.context.on("page", self.track_navigation)
        self.page = await self.context.new_page()

    def track_navigation(self, page):
        def request_started(request):
            if request.is_navigation_request() and request.frame == page.main_frame:
                self.navigation_methods[page] = request.method
        page.on("request", request_started)

    async def refresh_page(self):
        async with self.lock:
            if not safe_resume_url(self.page.url):
                raise ValueError("Halaman ini harus dimuat ulang secara manual")
            if self.navigation_methods.get(self.page, "GET") != "GET":
                raise ValueError("Halaman berasal dari pengiriman formulir; periksa manual sebelum memuat ulang")
            await self.page.reload(wait_until="domcontentloaded")

    async def maintain(self, job, saved, attention):
        if job.action not in {"open_session", "refresh_session", "check_account", "check_topup"}:
            raise RuntimeError("This live action has not yet been mapped and verified")
        if job.action == "check_topup" and not self.saved_card_verified:
            raise RuntimeError("Saved-card checkout is not verified")
        await self.open(saved)
        if job.action in {"check_account", "check_topup"}:
            await self.page.goto("https://platform.claude.com/dashboard", wait_until="domcontentloaded")
            while True:
                content = await self.page.locator("body").inner_text()
                result = recognize_account(self.page.url, content[:120_000])
                if result != "unknown":
                    if result == "ready_for_topup" and job.action == "check_topup":
                        return await self.buy_with_saved_card(job, attention)
                    return result
                await attention(job, "Status dashboard belum jelas; buka worker untuk memeriksa login")
        url = safe_resume_url(self.resume_url)
        if job.action == "refresh_session" and not url:
            raise ValueError("No saved page to refresh")
        await self.page.goto(url or "https://platform.claude.com/", wait_until="domcontentloaded")
        await self.attend_current_page(job, attention)
        return "page_refreshed" if job.action == "refresh_session" else "session_opened"

    async def buy_with_saved_card(self, job, attention):
        # Must verify the linked method, USD amount, final charge within limit,
        # one submission, challenge state, and transaction confirmation before
        # this adapter can be enabled. Never refill or replace a card here.
        raise RuntimeError("Saved-card checkout requires a verified live mapping")

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
        email_sent = password_sent = google_clicked = False
        while not job.finish_requested:
            pages = [p for p in self.context.pages if not p.is_closed()]
            if not pages:
                raise RuntimeError("Browser closed")
            if self.page.is_closed():
                self.page = pages[-1]
            # OAuth can open a popup. Prefer it until Google returns to Claude.
            google_pages = [p for p in pages if urlparse(p.url).hostname == "accounts.google.com"]
            if google_pages:
                self.page = google_pages[-1]
            host = urlparse(self.page.url).hostname
            try:
                if host == "accounts.google.com":
                    email = self.page.locator('input[type="email"]')
                    password = self.page.locator('input[type="password"]')
                    if not email_sent and await self.visible(email):
                        await email.fill(account.email)
                        email_sent = True
                        await self.page.locator("#identifierNext").click()
                        await asyncio.sleep(2)
                        continue
                    if not password_sent and await self.visible(password):
                        await password.fill(account.password.get_secret_value())
                        password_sent = True
                        await self.page.locator("#passwordNext").click()
                        await asyncio.sleep(2)
                        continue
                    await attention(job, "Login Google perlu tindakan: buka browser, selesaikan login, tutup kontrol lalu Lanjutkan")
                    continue
                if host in {"platform.claude.com", "console.anthropic.com"}:
                    google = self.page.get_by_role("button", name=re.compile(r"^(Continue with Google|Lanjutkan dengan Google)$", re.I))
                    if not google_clicked and await self.visible(google):
                        google_clicked = True
                        await google.click()
                        await asyncio.sleep(2)
                        continue
                    if await self.handle_organization_picker():
                        await asyncio.sleep(1)
                        continue
                    kind = await self.inspect_state()
                    if kind not in {PageKind.unknown, PageKind.organization_picker}:
                        await attention(job, ATTENTION_MESSAGES.get(kind, ATTENTION_MESSAGES[PageKind.unknown]))
                        continue
                    # Only public, uniquely labelled fields on the official page
                    # or its Stripe frames are populated. Never submit a charge.
                    count = await self.fill_identity(batch.identity)
                    count += await self.fill_amount(batch.amount_usd)
                    card_ready = await self.fill_card(batch.card)
                    limit = batch.total_limit_usd / len(batch.accounts)
                    message = (f"Target kredit USD {batch.amount_usd}; batas per akun USD {limit:.2f}. "
                               "Periksa ringkasan tagihan. Pembelian belum dikirim otomatis. "
                               "Buka browser untuk tahap berikutnya; Simpan & tutup sesi setelah selesai.")
                    if count or card_ready:
                        message = "Kolom yang dikenali sudah diisi. " + message
                    await attention(job, message)
                    continue
                await self.attend_current_page(job, attention)
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                raise
            except Exception:
                # Don't leak DOM, URLs or values from Playwright exceptions.
                await attention(job, "Langkah otomatis belum berhasil; buka browser untuk melanjutkan manual")
        return "session_saved_unverified"

    async def fill_identity(self, identity):
        fields = {
            "full_name": r"^(Full name|Name|Nama lengkap|Nama)$",
            "organization": r"^(Organization(?: name)?|Company(?: name)?|Nama organisasi|Organisasi)$",
            "address": r"^(Address line 1|Street address|Baris alamat 1|Alamat)$",
            "city": r"^(City|Kota)$",
            "region": r"^(State|Province|Region|Provinsi|Wilayah)$",
            "postal_code": r"^(ZIP(?: code)?|Postal code|Kode pos)$",
            "country": r"^(Country(?: or region)?|Negara(?: atau wilayah)?)$",
        }
        filled = 0
        for frame in self.page.frames:
            host = urlparse(frame.url).hostname or ""
            if urlparse(frame.url).scheme != "https" or not (
                host in {"platform.claude.com", "console.anthropic.com", "stripe.com"} or host.endswith(".stripe.com")
            ):
                continue
            for field, pattern in fields.items():
                locator = frame.get_by_label(re.compile(pattern, re.I))
                if not await self.visible(locator) or not await locator.is_enabled():
                    continue
                tag = await locator.evaluate("el => el.tagName.toLowerCase()")
                value = getattr(identity, field)
                value = value if isinstance(value, str) else value.get_secret_value()
                if not value:
                    continue
                if tag == "select":
                    # Custom country widgets must be selected by the operator.
                    if await locator.locator("option").evaluate_all("(opts,v) => opts.some(o => o.value === v)", value):
                        await locator.select_option(value=value)
                        filled += 1
                elif tag in {"input", "textarea"} and await locator.is_editable():
                    await locator.fill(value)
                    filled += 1
        return filled

    async def fill_amount(self, amount):
        # Screenshots do not show the amount selector. Populate only a uniquely
        # labelled amount field; unknown controls remain available to the admin.
        locator = self.page.get_by_label(re.compile(r"^(Credit amount|Amount in USD|Jumlah kredit|Nominal kredit)$", re.I))
        if await self.visible(locator) and await locator.is_editable():
            await locator.fill(str(amount))
            return 1
        return 0

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
            owner = urlparse(page.url)
            if owner.scheme != "https" or owner.hostname not in {"platform.claude.com", "console.anthropic.com"}:
                continue
            for frame in page.frames:
                host = urlparse(frame.url).hostname or ""
                if host != "stripe.com" and not host.endswith(".stripe.com"):
                    continue
                for field, selector in selectors.items():
                    locator = frame.locator(selector)
                    if await self.visible(locator):
                        matches[field].append(locator)
        if not all(len(locators) == 1 for locators in matches.values()):
            return False
        await matches["number"][0].fill(card.number.get_secret_value())
        await matches["expiry"][0].fill(card.expiry)
        await matches["cvv"][0].fill(card.cvv.get_secret_value())
        return True

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
