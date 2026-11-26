"""Exercise a share-target navigation against Duet's HTTPS listener in Chromium."""

import asyncio
import base64
import hashlib
import os
import socket
import ssl
from pathlib import Path
from tempfile import TemporaryDirectory

from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from playwright.async_api import Page, async_playwright, expect

from duet.core._network import select_bind_address
from duet.core.config import Config
from duet.core.pairing import PairingManager
from duet.core.tls import prepare_tls
from duet.server.app import create_app
from duet.server.text import TEXT


async def browser_pin(host: str, port: int, public_root: Path) -> str:
    root = x509.load_der_x509_certificate(public_root.read_bytes())
    trust = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    trust.load_verify_locations(cadata=root.public_bytes(Encoding.PEM).decode("ascii"))
    _, writer = await asyncio.open_connection(host, port, ssl=trust)
    try:
        peer = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
        key = x509.load_der_x509_certificate(peer).public_key()
        spki = key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        return base64.b64encode(hashlib.sha256(spki).digest()).decode("ascii")
    finally:
        writer.close()
        await writer.wait_closed()


async def submit_share(page: Page, manifest: dict) -> None:
    # Android sends a multipart navigation to the manifest's action. Build that
    # same navigation without depending on an OS share chooser in headless CI.
    await page.evaluate(
        """target => {
            const form = document.createElement("form");
            form.action = target.action;
            form.method = target.method;
            form.enctype = target.enctype;
            const text = document.createElement("input");
            text.name = target.params.text;
            text.value = "Generated shared text";
            const files = document.createElement("input");
            files.type = "file";
            files.name = target.params.files[0].name;
            const data = new DataTransfer();
            data.items.add(new File(["Generated shared file"], "shared.txt",
                {type: "text/plain"}));
            files.files = data.files;
            form.append(text, files);
            document.body.append(form);
            form.submit();
        }""",
        manifest["share_target"],
    )


async def main() -> None:
    host = select_bind_address()
    with TemporaryDirectory() as directory, socket.socket() as sock:
        root = Path(directory)
        os.environ.update(LOCALAPPDATA=directory, XDG_DATA_HOME=directory)
        received = root / "Received"
        identity = prepare_tls(host, directory=root / "tls")
        sock.bind((host, 0))
        port = sock.getsockname()[1]
        url = f"https://{host}:{port}"
        pairing = PairingManager()
        offer = pairing.issue_offer()
        app = create_app(
            pairing,
            host=host,
            port=port,
            https=True,
            config=Config(received_directory=received),
        )
        runner = web.AppRunner(app, access_log=None, shutdown_timeout=1)
        await runner.setup()
        try:
            await web.SockSite(runner, sock, ssl_context=identity.context).start()
            pin = await browser_pin(host, port, identity.root_certificate)
            async with async_playwright() as driver:
                browser = await driver.chromium.launch(
                    executable_path=os.getenv("DUET_TEST_BROWSER"),
                    args=[
                        "--no-proxy-server",
                        f"--ignore-certificate-errors-spki-list={pin}",
                    ],
                )
                try:
                    page = await browser.new_page(
                        viewport={"width": 393, "height": 852}, is_mobile=True
                    )
                    await page.goto(url, wait_until="domcontentloaded")
                    assert await page.evaluate("isSecureContext")
                    manifest = await page.evaluate(
                        """async () => {
                            const link = document.querySelector('link[rel="manifest"]');
                            const res = await fetch(link.href);
                            if (!res.ok) throw Error("Manifest unavailable");
                            return res.json();
                        }"""
                    )
                    await page.evaluate(
                        "navigator.serviceWorker.ready.then(() => true)"
                    )
                    await page.reload(wait_until="domcontentloaded")
                    await page.wait_for_function(
                        "navigator.serviceWorker.controller !== null"
                    )
                    await submit_share(page, manifest)
                    confirm = page.get_by_role("button", name="Send to PC", exact=True)
                    await expect(confirm).to_be_disabled()
                    await page.reload(wait_until="domcontentloaded")
                    await expect(confirm).to_be_disabled()
                    await page.get_by_label("6-digit PIN").fill(offer.pin)
                    assert (
                        not app[TEXT].messages()
                        and not (received / "shared.txt").exists()
                    )
                    await page.get_by_role("button", name="Connect", exact=True).click()
                    await expect(confirm).to_be_enabled()
                    assert (
                        not app[TEXT].messages()
                        and not (received / "shared.txt").exists()
                    )
                    await confirm.click()
                    async with asyncio.timeout(30):
                        while (
                            not (received / "shared.txt").exists()
                            or not app[TEXT].messages()
                        ):
                            await asyncio.sleep(0.1)
                    assert (
                        received / "shared.txt"
                    ).read_bytes() == b"Generated shared file"
                    assert app[TEXT].messages()[0]["text"] == "Generated shared text"
                    # Exercise tap-to-copy without changing the host's clipboard.
                    await page.evaluate(
                        """() => Object.defineProperty(navigator, "clipboard", {
                            value: {writeText: async text => {window.copied = text}}
                        })"""
                    )
                    await page.get_by_role("button", name="Copy", exact=True).click()
                    assert (
                        await page.evaluate("window.copied") == "Generated shared text"
                    )
                finally:
                    await browser.close()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=90))
