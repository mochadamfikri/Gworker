"""Real Chromium tests against local fixtures, never live Google/Claude accounts."""
import json
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from antwork.browser import BrowserDriver
from antwork.models import Control

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
