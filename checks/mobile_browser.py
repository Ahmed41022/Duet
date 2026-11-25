"""Exercise the bundled phone client over LAN HTTP in a mobile Chromium viewport."""

import asyncio
import os
import socket
from pathlib import Path
from tempfile import TemporaryDirectory

from aiohttp import web
from playwright.async_api import async_playwright, expect

from duet.core._network import select_bind_address
from duet.core.config import Config
from duet.core.pairing import PairingManager
from duet.server.app import create_app


async def main() -> None:
    host = select_bind_address()
    with TemporaryDirectory() as directory, socket.socket() as sock:
        root = Path(directory)
        os.environ.update(LOCALAPPDATA=str(root), XDG_DATA_HOME=str(root))
        sock.bind((host, 0))
        port = sock.getsockname()[1]
        url = f"http://{host}:{port}"
        pairing = PairingManager()
        offer = pairing.issue_offer()
        received = root / "received"
        app = create_app(
            pairing, host=host, port=port, config=Config(received_directory=received)
        )
        runner = web.AppRunner(app, access_log=None, shutdown_timeout=1)
        await runner.setup()
        try:
            await web.SockSite(runner, sock).start()
            async with async_playwright() as driver:
                browser = await driver.chromium.launch(
                    executable_path=os.getenv("DUET_TEST_BROWSER"),
                    args=["--no-proxy-server"],
                )
                try:
                    page = await browser.new_page(
                        viewport={"width": 393, "height": 852}, is_mobile=True
                    )
                    await page.goto(
                        f"{url}/#token={offer.token}", wait_until="domcontentloaded"
                    )
                    files = page.locator(
                        'input[type="file"]:not([webkitdirectory])'
                    ).first
                    await files.wait_for(state="attached")
                    assert "#" not in page.url and not await page.evaluate(
                        "isSecureContext"
                    )
                    data = bytes(range(256)) * 200
                    await files.set_input_files(
                        {
                            "name": "phone.bin",
                            "mimeType": "application/octet-stream",
                            "buffer": data,
                        }
                    )
                    async with asyncio.timeout(30):
                        while not (received / "phone.bin").exists():
                            await asyncio.sleep(0.1)
                    assert (received / "phone.bin").read_bytes() == data
                    await page.locator("textarea").fill("Hello from the phone")
                    await (
                        page.locator("form")
                        .filter(has=page.locator("textarea"))
                        .locator("button")
                        .click()
                    )
                    await expect(
                        page.get_by_text("Hello from the phone", exact=True)
                    ).to_be_visible()
                    await page.get_by_role("button", name="العربية").click()
                    await expect(page.locator("html")).to_have_attribute("dir", "rtl")
                    assert await page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth"
                    )
                finally:
                    await browser.close()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
