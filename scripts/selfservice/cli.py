"""``selfservice`` — shortcuts for the self-service track. Prefer ``uv run deploy``.

  uv run selfservice up [--automated] [--with-labs]    # = uv run deploy --track self-service ...
  uv run selfservice down [--yes] [--delete-credentials]  # = uv run destroy --only self-service ...

Kept because the self-service walkthrough and attendees' muscle memory use them.
Both forward to the one implementation, so they can't drift from it.
"""

from __future__ import annotations

import argparse

import deploy
from scripts.common import destroy


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="selfservice", description="Self-service shortcuts (prefer `uv run deploy`)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_up = sub.add_parser("up", help="= uv run deploy --track self-service")
    p_up.add_argument("--automated", action="store_true", help="Use credentials.env values without prompting")
    p_up.add_argument("--with-labs", action="store_true", help="Also prebuild LAB 3 + LAB 4")

    p_down = sub.add_parser("down", help="= uv run destroy --only self-service")
    p_down.add_argument("--yes", action="store_true", help="Skip every prompt (credentials.env is kept)")
    p_down.add_argument("--delete-credentials", action="store_true", help="Also delete credentials.env")

    args = parser.parse_args(argv)
    if args.command == "up":
        deploy.main(
            ["--track", "self-service"]
            + (["--automated"] if args.automated else [])
            + (["--with-labs"] if args.with_labs else [])
        )
    else:
        destroy.main(
            ["--only", "self-service"]
            + (["--yes"] if args.yes else [])
            + (["--delete-credentials"] if args.delete_credentials else [])
        )


if __name__ == "__main__":
    main()
