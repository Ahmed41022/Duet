"""Command-line entry point for Duet."""

import argparse

from duet import __version__


def main() -> None:
    parser = argparse.ArgumentParser(description="Local phone and PC sharing.")
    parser.add_argument("--version", action="version", version=f"duet {__version__}")
    parser.parse_args()
    parser.print_help()
