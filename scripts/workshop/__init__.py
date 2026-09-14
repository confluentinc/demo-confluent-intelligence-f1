"""Workshop credential/health tools that pair with `wsa` (workshop-setup-accelerator).

Provisioning N isolated attendee environments and tearing them down is `wsa
build` / `wsa clean` (see confluentinc/workshop-setup-accelerator, run from a
sibling checkout against this repo's `wsa-spec-aws.yaml`) — attendees receive
scoped API keys via the wsa dispenser, never a Console login.

Entry points (see pyproject.toml [project.scripts]):
  workshop   -> scripts.workshop.cli:main       (creds / validate)
  f1-sql     -> scripts.workshop.sql_shell:main (attendee Flink SQL REPL)

Self-serve attendees don't run a tool for credentials: they save the dispenser's
Env File block verbatim as ./credentials.env (see docs/tracks/HOSTED-WORKSHOP.md).
"""
