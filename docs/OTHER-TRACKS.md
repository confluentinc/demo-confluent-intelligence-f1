# Other ways to run the demo

The instructor-led workshop is the primary path in this repo. Two solo tracks
remain available when a multi-attendee build would be excessive.

## Standalone AWS demo

Use the standalone track for one environment with the same AWS shape attendees
receive: ECS simulator, Postgres, CDC, and Confluent Cloud.

```bash
uv run deploy
uv run race status
uv run reset
uv run destroy
```

It needs Docker, AWS credentials, Terraform, Confluent Cloud credentials, and
Bedrock access. Read the complete [standalone guide](tracks/STANDALONE-DEMO.md).

## Self-service

Use self-service for a fast solo run without the shared AWS infrastructure. It
provisions Confluent Cloud resources, seeds history through Flink, and runs the
simulator locally.

```bash
uv run selfservice up
uv run f1-race
uv run f1-sql
uv run selfservice down
```

Read the complete [self-service guide](tracks/SELF-SERVICE.md). LAB 5 still needs
an IBM watsonx Orchestrate account.

Both tracks use the canonical SQL under `docs/demo-reference/` and follow the attendee
`README.md`. Their deployment and teardown commands remain separate from
the organizer workflow.
