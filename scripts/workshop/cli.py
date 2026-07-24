"""`workshop` CLI — creds / validate.

Provisioning and teardown are `wsa build` / `wsa clean` (see
confluentinc/workshop-setup-accelerator, run from a sibling checkout, driven
by this repo's `wsa-spec-aws.yaml`). This CLI covers what `wsa` doesn't:
turning its build-output.csv into the credential cards our own tools expect,
and fleet-wide health checks against those cards. See each subcommand module
for details.
"""

from __future__ import annotations

import argparse

from scripts.workshop import creds as creds_mod
from scripts.workshop import validate as validate_mod


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="workshop", description="F1 workshop credential/health tools (pairs with wsa)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_creds = sub.add_parser("creds", help="Generate attendee credential cards from a wsa build-output.csv")
    creds_mod.add_arguments(p_creds)
    p_creds.set_defaults(func=creds_mod.creds)

    p_validate = sub.add_parser("validate", help="Verify attendee environments (API-key checks)")
    validate_mod.add_arguments(p_validate)
    p_validate.set_defaults(func=validate_mod.validate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
