import asyncio
import hashlib
import json
import ssl
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import pytest
from aiohttp import ClientSession, web
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from duet.core.pairing import PairingManager
from duet.core.tls import pairing_url, prepare_tls, sharing_url
from duet.server.app import create_app

HOST = "192.168.1.2"
PORT = 8765
AUTHORITY = f"{HOST}:{PORT}"


def test_root_export_and_pairing_fragment(tmp_path):
    identity = prepare_tls(HOST, directory=tmp_path)
    public = identity.root_certificate.read_bytes()
    certificate = x509.load_der_x509_certificate(public)
    assert certificate.subject == certificate.issuer
    assert certificate.extensions.get_extension_for_class(
        x509.BasicConstraints
    ).value.ca
    assert identity.fingerprint == hashlib.sha256(public).digest().hex(":").upper()
    url = urlsplit(
        pairing_url(sharing_url(HOST, 443, https=True), "offer", identity.fingerprint)
    )
    assert url.scheme == "https"
    assert url.netloc == HOST
    assert not url.query
    assert parse_qs(url.fragment) == {
        "token": ["offer"],
        "fingerprint": [hashlib.sha256(public).hexdigest()],
    }


def test_concurrent_initialization_and_export_recovery_keep_same_trust(tmp_path):
    with ThreadPoolExecutor(max_workers=4) as pool:
        identities = list(
            pool.map(lambda _: prepare_tls(HOST, directory=tmp_path), range(8))
        )
    assert len({identity.fingerprint for identity in identities}) == 1
    paths = {path.name for path in tmp_path.iterdir()}
    assert paths == {"duet-root.cer", "root-private.pem"}
    public_path = identities[0].root_certificate
    public_path.unlink()
    renewed = prepare_tls("192.168.1.3", directory=tmp_path)
    assert renewed.fingerprint == identities[0].fingerprint


def test_https_validates_trust_origin_host_and_secure_session(tmp_path):
    async def check():
        identity = prepare_tls(HOST, directory=tmp_path / "identity")
        certificate = x509.load_der_x509_certificate(
            identity.root_certificate.read_bytes()
        )
        trust = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        trust.verify_flags |= ssl.VERIFY_X509_STRICT
        trust.load_verify_locations(
            cadata=certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")
        )
        untrusted = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        pairing = PairingManager()
        offer = pairing.issue_offer()
        static = tmp_path / "static"
        static.mkdir()
        (static / "index.html").write_text("Duet", encoding="utf-8")
        routes = {
            "/manifest.webmanifest": "application/manifest+json",
            "/sw.js": "text/javascript",
            "/icon-192.png": "image/png",
            "/icon-512.png": "image/png",
        }
        for route in routes:
            (static / route[1:]).write_bytes(b"asset")
        app = create_app(pairing, host=HOST, port=PORT, https=True, static_root=static)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        plain = web.TCPSite(runner, "127.0.0.1", 0)
        site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=identity.context)
        try:
            await plain.start()
            await site.start()
            plain_port = plain._server.sockets[0].getsockname()[1]
            tls_port = site._server.sockets[0].getsockname()[1]
            url = f"https://127.0.0.1:{tls_port}"
            authority = {"Host": AUTHORITY}
            async with ClientSession(headers=authority) as client:
                async with client.get(f"http://127.0.0.1:{plain_port}/") as response:
                    assert response.status == 400

                with pytest.raises(ssl.SSLCertVerificationError, match="issuer"):
                    await client.get(url, ssl=untrusted, server_hostname=HOST)
                with pytest.raises(ssl.SSLCertVerificationError, match="mismatch"):
                    await client.get(url, ssl=trust, server_hostname="192.168.1.3")

                tls = {"ssl": trust, "server_hostname": HOST}
                async with client.get(url, **tls) as response:
                    assert response.status == 200
                    assert await response.text() == "Duet"
                async with client.get(
                    url, headers={"Host": "other.invalid"}, **tls
                ) as response:
                    assert response.status == 400
                async with client.get(f"{url}/api/session", **tls) as response:
                    assert response.status == 401
                for route, media_type in routes.items():
                    async with client.get(f"{url}{route}", **tls) as response:
                        assert response.status == 200
                        assert response.content_type == media_type
                        assert response.headers["Cache-Control"] == "no-store"

                body = json.dumps({"token": offer.token})
                headers = {
                    "Content-Type": "application/json",
                    "Origin": f"http://{AUTHORITY}",
                }
                async with client.post(
                    f"{url}/api/pair", data=body, headers=headers, **tls
                ) as response:
                    assert response.status == 403
                headers["Origin"] = f"https://{AUTHORITY}"
                async with client.post(
                    f"{url}/api/pair", data=body, headers=headers, **tls
                ) as response:
                    assert response.status == 200
                    cookie = response.cookies["duet_session"]
                    assert cookie["secure"]
                    assert cookie["httponly"]
                    assert cookie["samesite"] == "Strict"
                headers = {"Cookie": f"duet_session={cookie.value}"}
                async with client.get(
                    f"{url}/api/session", headers=headers, **tls
                ) as response:
                    assert response.status == 200
        finally:
            await runner.cleanup()

    asyncio.run(check())
