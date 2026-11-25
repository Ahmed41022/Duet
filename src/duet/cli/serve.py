"""Start Duet on a private LAN interface and display a terminal pairing offer."""

import asyncio
import sys

import qrcode
from aiohttp import web

from duet.core._network import select_bind_address
from duet.core.pairing import PairingManager, PairingOffer
from duet.server.app import create_app


def run_serve(*, bind: str | None = None, port: int = 8765) -> int:
    try:
        host = select_bind_address(bind)
        asyncio.run(_listen(host, port))
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError) as exc:
        print(f"Could not start Duet: {exc}", file=sys.stderr)
        return 1
    return 0


def _print_offer(base_url: str, offer: PairingOffer) -> None:
    qr = qrcode.QRCode(border=2)
    qr.add_data(f"{base_url}/#token={offer.token}")
    print(f"Open {base_url}/ or scan the QR code. PIN: {offer.pin}", flush=True)
    qr.print_ascii(tty=False, invert=True)
    sys.stdout.flush()


async def _listen(host: str, port: int) -> None:
    pairing = PairingManager()
    refresh = asyncio.Event()
    base_url = f"http://{host}:{port}" if port != 80 else f"http://{host}"
    app = create_app(pairing, host=host, port=port, on_paired=refresh.set)
    runner = web.AppRunner(app, access_log=None, handler_cancellation=True)
    await runner.setup()
    try:
        site = web.TCPSite(runner, host=host, port=port)
        await site.start()
        while True:
            refresh.clear()
            _print_offer(base_url, pairing.issue_offer())
            try:
                await asyncio.wait_for(refresh.wait(), timeout=pairing.offer_ttl)
            except TimeoutError:
                pass
    finally:
        await runner.cleanup()
