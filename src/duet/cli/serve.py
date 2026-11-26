"""Start Duet on a private LAN interface and display a terminal pairing offer."""

import asyncio
import sys
from pathlib import Path

import qrcode
from aiohttp import web

from duet.core._network import NetworkInterface, select_network
from duet.core.config import Config, load_config
from duet.core.device_status import DeviceStatus
from duet.core.logging import configure_logging
from duet.core.pairing import PairingManager, PairingOffer
from duet.core.tls import pairing_url, prepare_tls, sharing_url
from duet.server.app import create_app
from duet.server.lifecycle import StopReason, install_lifecycle


def run_serve(
    *,
    bind: str | None = None,
    port: int = 8765,
    config_path: Path | None = None,
    https: bool = False,
) -> int:
    try:
        network = select_network(bind)
        config = load_config(config_path)
        configure_logging(config)
        asyncio.run(_listen(network, port, config, https=https))
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError) as exc:
        print(f"Could not start Duet: {exc}", file=sys.stderr)
        return 1
    return 0


def _print_offer(
    base_url: str, offer: PairingOffer, fingerprint: str | None = None
) -> None:
    qr = qrcode.QRCode(border=2)
    qr.add_data(pairing_url(base_url, offer.token, fingerprint))
    print(f"Open {base_url}/ or scan the QR code. PIN: {offer.pin}", flush=True)
    qr.print_ascii(tty=False, invert=True)
    sys.stdout.flush()


async def _listen(
    network: NetworkInterface, port: int, config: Config, *, https: bool = False
) -> None:
    host = network.address
    print(f"Using {network.name} ({network.kind}) at {host}")
    if network.warning:
        print(network.warning)
    print("Allow incoming connections on trusted private networks only.")
    pairing = PairingManager()
    refresh = asyncio.Event()
    base_url = sharing_url(host, port, https=https)
    app = create_app(
        pairing,
        host=host,
        port=port,
        https=https,
        on_paired=refresh.set,
        config=config,
        device_status=DeviceStatus(),
    )
    identity = prepare_tls(host) if https else None
    stopped: StopReason | None = None

    def stop(reason: StopReason) -> None:
        nonlocal stopped
        stopped = reason
        refresh.set()

    install_lifecycle(app, host, config.idle_timeout_seconds, stop)
    runner = web.AppRunner(app, access_log=None, handler_cancellation=True)
    await runner.setup()
    try:
        site = web.TCPSite(
            runner,
            host=host,
            port=port,
            ssl_context=identity.context if identity else None,
        )
        await site.start()
        if identity:
            print(f"Duet root certificate: {identity.root_certificate}")
            print(f"Root certificate SHA-256: {identity.fingerprint}")
            print(
                "Verify and manually trust only this public certificate on the phone."
            )
            print(
                "Phone trust guide: https://github.com/Ahmed41022/Duet/blob/main/docs/https.md"
            )
        while stopped is None:
            refresh.clear()
            _print_offer(
                base_url,
                pairing.issue_offer(),
                identity.fingerprint if identity else None,
            )
            try:
                await asyncio.wait_for(refresh.wait(), timeout=pairing.offer_ttl)
            except TimeoutError:
                pass
        print(
            "Stopped after inactivity."
            if stopped == "idle"
            else "Network address changed. Restart sharing to pair again."
        )
    finally:
        await runner.cleanup()
