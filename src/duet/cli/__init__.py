"""Command-line entry point for Duet."""

import argparse
from pathlib import Path

from duet import __version__


def main() -> None:
    parser = argparse.ArgumentParser(description="Local phone and PC sharing.")
    parser.add_argument("--version", action="version", version=f"duet {__version__}")
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser(
        "serve", help="Start the local server and show pairing details"
    )
    serve.add_argument("--bind", help="Assigned private IPv4 address")
    serve.add_argument("--port", type=int, default=8765)

    serve.add_argument(
        "--config", type=Path, help="Path to the Duet TOML configuration"
    )
    send = commands.add_parser("send", help="Offer a file or folder to paired phones")
    send.add_argument("file", type=Path)
    commands.add_parser("devices", help="List paired devices on active local servers")
    commands.add_parser("history", help="List recent PC transfer history")
    args = parser.parse_args()
    if args.command == "devices":
        from duet.core.device_status import list_devices

        devices = list_devices()
        for device in devices:
            print(device["id"], device["address"])
        if not devices:
            print("No paired devices.")
        return
    if args.command == "history":
        from duet.core.history import HistoryStore

        for item in HistoryStore().list():
            print(item["completed_at"], item["direction"], item["status"], item["name"])
        return
    if args.command == "send":
        from duet.cli.send import run_send

        raise SystemExit(run_send(args.file))
    if args.command == "serve":
        from duet.cli.serve import run_serve

        raise SystemExit(
            run_serve(bind=args.bind, port=args.port, config_path=args.config)
        )
    parser.print_help()
