"""Real Chromium tests against local fixtures, never live Google/Claude accounts."""
import json
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from antwork.browser import BrowserDriver
from antwork.models import Control
from antwork.recognition import PageKind

STATIC = Path(__file__).parents[1] / "antwork" / "static"


async def test_panel_import_validation_and_mobile():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width":1440,"height":1000})
        errors = []
        page.on("pageerror",lambda error: errors.append(str(error)))
        submitted = []

        async def route_handler(route):
            path = route.request.url.replace("https://antwork.test", "")
            if path == "/api/state":
                return await route.fulfill(json={"jobs":[],"active_batch":None,"concurrency":3,"max_concurrency":5,"live_enabled":True})
            if path == "/api/address":
                return await route.fulfill(json={"address":None})
            if path == "/api/batches":
                submitted.append(json.loads(route.request.post_data))
                return await route.fulfill(status=201,json={"batch_id":"test"})
            if path in {"/", "/static/app.js", "/static/style.css"}:
                name = "index.html" if path == "/" else path.rsplit("/",1)[1]
                return await route.fulfill(path=str(STATIC / name))
            await route.abort()
        await page.route("**/*",route_handler)
        await page.goto("https://antwork.test/")
        await page.get_by_role("button",name="＋ Buat batch baru").click()
        await page.locator("#account-file").set_input_files({"name":"accounts.csv","mimeType":"text/csv","buffer":b'"one@example.com:pass:with:colon"\r\n"two@example.com:another"\r\n'})
        await page.wait_for_function("document.querySelector('#accounts').value.includes('one@example.com')")
        fields = {"full-name":"Test Person","organization":"Org","address":"Test Street","city":"Test City","region":"Region","postal-code":"12345","country":"ID","card-holder":"Person","card-number":"4242424242424242","expiry":"12/39","cvv":"123","amount":"5.00","total-limit":"10.00"}
        for field,value in fields.items():
            await page.locator(f"#{field}").fill(value)
        await page.locator("#consent").check()
        await page.get_by_role("button",name="Start workers").click()
        await page.wait_for_function("!document.querySelector('#batch-dialog').open")
        assert len(submitted) == 1
        assert submitted[0]["accounts"][0]["password"] == "pass:with:colon"
        assert await page.locator("#card-number").input_value() == ""
        assert await page.locator("#accounts").input_value() == ""
        await page.set_viewport_size({"width":390,"height":844})
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors
        await browser.close()


async def test_remote_input_and_browser_isolation():
    driver = BrowserDriver()
    try:
        await driver.open()
        await driver.page.set_content('<input aria-label="OTP"><button>Confirm</button>')
        await driver.page.get_by_label("OTP").focus()
        await driver.control(Control(kind="text",text="123456"))
        assert await driver.page.get_by_label("OTP").input_value() == "123456"
        assert (await driver.frame()).startswith(b"\xff\xd8")
        with pytest.raises(ValueError):
            await driver.control(Control(kind="key",key="Control+l"))
        await driver.context.add_cookies([{"name":"test","value":"private","domain":"example.com","path":"/"}])
        other = await driver.browser.new_context()
        assert await other.cookies() == []
        await other.close()
    finally:
        await driver.close()


@pytest.mark.parametrize("viewport", [{"width":390,"height":844},{"width":1280,"height":800}])
async def test_branch_recognition_in_frames_on_mobile_and_desktop(viewport):
    driver = BrowserDriver()
    try:
        await driver.open()
        await driver.page.set_viewport_size(viewport)
        async def route(route):
            if route.request.url == "https://platform.claude.com/":
                await route.fulfill(content_type="text/html",body='<iframe src="https://bank.test/challenge"></iframe>')
            elif route.request.url == "https://bank.test/challenge":
                await route.fulfill(content_type="text/html",body='<p>ID Check. Kode Otentikasi Mastercard. ANTHROPIC USD 0,00</p>')
            else:
                await route.fulfill(content_type="text/html",body='<h1>Continue on another device</h1><p>Scan the QR code</p><button>Send Email</button>')
        await driver.context.route("**/*",route)
        await driver.page.goto("https://platform.claude.com/")
        assert await driver.inspect_state() == PageKind.bank_otp
        popup = await driver.context.new_page()
        await popup.goto("https://inquiry.withpersona.com/test")
        assert await driver.inspect_state() == PageKind.persona_device
        assert driver.page is popup
        await popup.set_content('<h1>Tautan kedaluwarsa</h1>')
        assert await driver.inspect_state() == PageKind.persona_expired
        await popup.set_content('<h1>Selamat, Anda sudah selesai!</h1><p>Terima kasih telah memverifikasi identitas Anda.</p>')
        assert await driver.inspect_state() == PageKind.persona_complete
    finally:
        await driver.close()


async def test_retry_refreshes_only_selected_worker_and_does_not_repeat_post():
    driver = BrowserDriver()
    other = BrowserDriver()
    requests = []
    other_requests = []
    try:
        await driver.open()
        await other.open()
        async def route(route):
            requests.append(route.request.method)
            await route.fulfill(content_type="text/html",body='<form method="post" action="/submitted"><button>Submit fixture</button></form>')
        async def other_route(route):
            other_requests.append(route.request.method)
            await route.fulfill(content_type="text/html",body='Other worker')
        await driver.context.route("**/*",route)
        await other.context.route("**/*",other_route)
        await driver.page.goto("https://platform.claude.com/dashboard")
        await other.page.goto("https://platform.claude.com/dashboard")
        await driver.refresh_page()
        assert requests == ["GET","GET"]
        assert other_requests == ["GET"]
        async with driver.page.expect_navigation():
            await driver.page.get_by_role("button",name="Submit fixture").click()
        assert requests[-1] == "POST"
        before = len(requests)
        with pytest.raises(ValueError):
            await driver.refresh_page()
        assert len(requests) == before
    finally:
        await driver.close()
        await other.close()


async def test_check_topup_dialog_uses_linked_card_without_collecting_cvv():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        submitted = []
        async def route_handler(route):
            path = route.request.url.replace("https://antwork.test", "")
            if path == "/api/state":
                return await route.fulfill(json={"jobs":[{"id":"worker-one","email":"one@example.com","status":"completed","action":"purchase","phase":"Complete","session_status":"saved"}],"active_batch":None,"concurrency":3,"max_concurrency":5,"live_enabled":False,"saved_card_topup_enabled":True})
            if path == "/api/jobs/worker-one/topup":
                submitted.append(json.loads(route.request.post_data))
                return await route.fulfill(status=202,json={"job_id":"topup-one"})
            if path in {"/", "/static/app.js", "/static/style.css"}:
                name = "index.html" if path == "/" else path.rsplit("/",1)[1]
                return await route.fulfill(path=str(STATIC / name))
            await route.abort()
        await page.route("**/*",route_handler)
        await page.goto("https://antwork.test/")
        await page.get_by_role("button",name="Cek",exact=True).click()
        await page.locator("#topup-amount").fill("5.00")
        await page.locator("#topup-limit").fill("6.00")
        assert await page.locator('#topup-dialog input[type="password"]').count() == 0
        await page.get_by_role("button",name="Cek & isi saldo",exact=True).click()
        await page.wait_for_function("!document.querySelector('#topup-dialog').open")
        assert len(submitted) == 1
        assert set(submitted[0]) == {"request_id","amount_usd","limit_usd"}
        assert submitted[0]["amount_usd"] == "5.00"
        await browser.close()


async def test_worker_runs_google_then_checkout_and_saves_without_claiming_payment(payload):
    from antwork.engine import Job
    from antwork.models import BatchInput
    batch = BatchInput(**payload)
    driver = BrowserDriver()
    original_open = driver.open
    seen = []
    async def open_fixture(saved=None):
        await original_open(saved)
        async def route(route):
            url = route.request.url
            if url == 'https://platform.claude.com/':
                body = '<button onclick="location.href=\'https://accounts.google.com/login\'">Continue with Google</button>'
            elif url.endswith('/login'):
                body = '<input type="email"><button id="identifierNext" onclick="location.href=\'/password\'">Next</button>'
            elif url.endswith('/password'):
                body = '<input type="password"><button id="passwordNext" onclick="location.href=\'https://platform.claude.com/dashboard\'">Next</button>'
            elif 'stripe.com' in url:
                body = '<input autocomplete="cc-number"><input autocomplete="cc-exp"><input autocomplete="cc-csc">'
            else:
                body = '<label>Nama lengkap<input></label><label>Kota<input></label><label>Jumlah kredit<input></label><iframe src="https://js.stripe.com/payment"></iframe><button onclick="document.body.dataset.charged=\'yes\'">Beli kredit</button>'
            await route.fulfill(content_type='text/html', body=body)
        await driver.context.route('**/*',route)
    driver.open = open_fixture
    async def attention(job, message):
        seen.append(message)
        assert driver.page.url == 'https://platform.claude.com/dashboard'
        assert await driver.page.get_by_label('Nama lengkap').input_value() == 'Test Person'
        assert await driver.page.get_by_label('Jumlah kredit').input_value() == '5.00'
        stripe = driver.page.frames[1]
        assert await stripe.locator('[autocomplete="cc-number"]').input_value() == '4242424242424242'
        assert await driver.page.locator('body').get_attribute('data-charged') is None
        job.finish_requested = True
    try:
        outcome = await driver.run(Job('one','batch','test0@example.com'),batch.accounts[0],batch,attention)
        assert outcome == 'session_saved_unverified'
        assert len(seen) == 1
    finally:
        await driver.close()
