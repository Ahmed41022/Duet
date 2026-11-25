"""Command-line entry point for Duet."""

import argparse

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

    args = parser.parse_args()
    if args.command == "serve":
        from duet.cli.serve import run_serve

        raise SystemExit(run_serve(bind=args.bind, port=args.port))
    parser.print_help()
