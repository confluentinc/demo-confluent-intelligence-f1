"""Compatibility entry point for manifest-backed workshop resets."""

from __future__ import annotations

import argparse

from scripts.workshop.lifecycle import add_lifecycle_arguments, reset_races


def add_arguments(parser: argparse.ArgumentParser) -> None:
    add_lifecycle_arguments(parser)


def main() -> None:
    parser = argparse.ArgumentParser(prog="workshop reset-races")
    add_arguments(parser)
    reset_races(parser.parse_args())


if __name__ == "__main__":
    main()
