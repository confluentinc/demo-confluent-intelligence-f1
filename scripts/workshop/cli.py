"""`workshop` CLI — the organizer namespace.

Everything an instructor does to a *fleet* of attendee environments lives here,
so the top-level `uv run` scripts stay the solo/attendee surface (`f1-sql`,
`f1-pitwall`, `deploy`, `selfservice`).

Three groups of subcommands:

  provision   spec-validate / build / clean — wrappers around the `wsa` CLI in a
              sibling checkout, which owns provisioning and teardown. `build`
              also writes the credential cards, so the run-id never has to be
              copied by hand. See `wsa.py`.
  credentials creds — turn a wsa build-output.csv into the cards our attendee
              tools authenticate with. `validate` then health-checks those
              cards against the live environments.
  race feeds  start-races / stop-races — scale every attendee's simulator ECS
              service at once. `start-all-races` / `stop-all-races` remain as
              deprecated aliases for the same code.

Note the two "validate" flavours, which check different things at different
times: `spec-validate` is wsa's pre-flight on the spec and local tooling
*before* a build; `validate` probes provisioned environments *after* one, using
each attendee's own API keys.
"""

from __future__ import annotations

import argparse

from scripts.instructor import start_all_races as start_mod
from scripts.instructor import stop_all_races as stop_mod
from scripts.workshop import creds as creds_mod
from scripts.workshop import validate as validate_mod
from scripts.workshop import wsa as wsa_mod


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="workshop",
        description="F1 workshop organizer tools — provision (via wsa), credential cards, race-feed control",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_spec = sub.add_parser("spec-validate", help="Pre-flight the wsa spec + local prerequisites (wsa validate)")
    wsa_mod.configure_spec_validate_parser(p_spec)
    p_spec.set_defaults(func=wsa_mod.spec_validate)

    p_build = sub.add_parser("build", help="Provision every attendee via wsa, then write their credential cards")
    wsa_mod.add_build_arguments(p_build)
    p_build.set_defaults(func=wsa_mod.build)

    p_clean = sub.add_parser("clean", help="Tear down a wsa run (run-id resolved from wsa-output/)")
    wsa_mod.add_clean_arguments(p_clean)
    p_clean.set_defaults(func=wsa_mod.clean)

    p_creds = sub.add_parser("creds", help="Generate attendee credential cards from a wsa build-output.csv")
    creds_mod.add_arguments(p_creds)
    p_creds.set_defaults(func=creds_mod.creds)

    p_validate = sub.add_parser("validate", help="Health-check provisioned attendee environments (API-key checks)")
    validate_mod.add_arguments(p_validate)
    p_validate.set_defaults(func=validate_mod.validate)

    p_start = sub.add_parser("start-races", help="Scale every attendee race simulator up")
    start_mod.add_arguments(p_start)
    p_start.set_defaults(func=start_mod.start_races)

    p_stop = sub.add_parser("stop-races", help="Scale every attendee race simulator to zero")
    stop_mod.add_arguments(p_stop)
    p_stop.set_defaults(func=stop_mod.stop_races)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
