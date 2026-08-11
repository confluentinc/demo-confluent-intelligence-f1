# Watsonx shared live-feed execution plan

Status: approved direction, not implemented yet.

## Outcome

All attendees use one Watsonx OpenAPI tool backed by account 50. The feed changes
as account 50's race progresses. Attendees don't enter a prefix, create a Watsonx
connection, handle Confluent credentials, or run local commands.

The organizer keeps account 50's Kafka and Schema Registry credentials. The
public API exposes only read-only race data.

```text
f1wp050 Kafka topics
        |
        v
organizer f1-social-feed process
        |
        v
https://small-underpass-refinery.ngrok-free.dev
        |
        v
one identical OpenAPI file -> every attendee's Watsonx agent
```

## Decisions

- Use account 50 as the shared Watsonx data source for the first delivery.
- Keep account 50 in the same Terraform baseline and lifecycle as accounts 1-49.
- Treat account 50 as organizer-controlled during the event. Don't assign its
  Confluent login to an attendee while it supplies the shared feed.
- If the workshop needs 50 attendee seats, provision account 51 before sending
  claim emails rather than letting an attendee modify account 50.
- Use the Kafka-backed `f1-social-feed`; don't use `f1-social-feed-rtce`.
- Use the assigned ngrok development domain for the first delivery.
- Give everyone the same OpenAPI file. It hardcodes
  `GET /race-feed/f1wp050`, so Watsonx exposes `get_race_feed` with no input.
- Accept that Lab 5 reads the organizer race, not each attendee's own Lab 3/4
  output.

## Safety boundary

This work must not provision or destroy a Confluent environment. It reads the
existing account-50 topics and changes repository code, the dispenser output,
and organizer-side processes only.

Before every live test:

1. Resolve account 50 from `runs/<run-id>/manifest.json`.
2. Use the manifest's exact credential-card path and ECS names.
3. Refuse ambiguous or missing manifest entries.
4. Never search AWS resources by substring.
5. Use an explicit run ID in rehearsal commands.
6. Record the other 49 accounts' ECS and Flink status before and after the test.

The current run is `f7zxf`. Its 50 environments are named
`RIVER-RACING-f1wp001-ENV` through `RIVER-RACING-f1wp050-ENV`. The read-only live
inventory check on 2026-08-11 found no separate `f1demo` environment.

## Phase 1: finish the cohort data-contract migration

The repository expects `race_id` and `event_time`, but the live cohort still uses
the older simulator image and table schemas. Finish the controlled, target-only
migration before treating the shared feed as workshop-ready.

Required gate:

- Capture `SHOW CREATE TABLE` for the live source tables before changing them.
- Build and push the new immutable simulator image first.
- Migrate source tables and task definitions with targeted plans/applies only.
- Re-enable RTCE registrations only after schemas settle.
- Run reset, start, late Lab 3, late Lab 4, and reset on account 50.
- Run the three-account isolation test on accounts 48-50.
- Reject any plan containing an unintended account or shared-stack replacement.

Do not run a full-stack Terraform apply.

## Phase 2: make one no-input Watsonx tool

Modify the existing `scripts/social_feed` code. Don't add another proxy.

1. Add configuration for a fixed public base URL and fixed feed prefix.
2. Keep the internal API route `/race-feed/{prefix}`.
3. Publish a Watsonx-specific OpenAPI document whose only operation is:

   ```text
   GET /race-feed/f1wp050
   operationId: get_race_feed
   parameters: none
   ```

4. Include the configured public URL under OpenAPI `servers`.
5. Exclude `/healthz` from the attendee OpenAPI document.
6. Serve the document as a download:

   ```text
   /watsonx/f1-race-feed-openapi.json
   ```

7. Set `Content-Disposition: attachment` with filename
   `f1-watsonx-race-feed.json`.
8. Keep Kafka/SR credentials out of the document, response, logs, and errors.

The attendee-facing tool description must say that the feed is shared and that
`live=false` means the race is paused or stopped. It must not call retained data
live merely because the consumer replayed it.

## Phase 3: make account 50 produce the full feed

At minimum, `race_standings` gives changing laps, positions, gaps, and headline
events. `car_state` and `pit_decisions` remain empty until Lab 3 and Lab 4 exist.

For the full workshop story, add an organizer command that reuses
`scripts/common/simulator_control.py` to prepare account 50's Lab 3 and Lab 4
objects. It must:

- Resolve only account 50 through the run manifest.
- Submit durable DDL separately from restartable `INSERT INTO` statements.
- Wait for the statements to reach their expected states.
- Print no credentials.
- Be safe to rerun.
- Leave the race stopped.

Proposed interface:

```bash
uv run workshop prepare-social-feed --run-id <run-id> --account 50
```

Run this after the cohort-wide `prepare-races`, because reset cancels active lab
statements. Account 50 is then intentionally different only for the duration of
Lab 5. A final account-50 reset removes that runtime exception and leaves it
stopped.

## Phase 4: start the organizer endpoint

Use two organizer processes:

```bash
uv run f1-social-feed \
  --creds runs/<run-id>/credentials/f1wp050.env \
  --public-base-url https://small-underpass-refinery.ngrok-free.dev \
  --fixed-prefix f1wp050

ngrok http 8080 \
  --url https://small-underpass-refinery.ngrok-free.dev
```

Add a wrapper only if it can supervise these existing processes, print the two
public URLs, verify account-50 freshness, and stop both cleanly. Don't build a
second HTTP service.

The organizer must disable laptop sleep, use stable power and network, and keep
both processes visible. Stop the public tunnel immediately after the workshop.

## Phase 5: distribute the tool through the dispenser

The same file works for every attendee, so no per-account generation is needed.

Add a common field to the WSA credential output:

```yaml
- name: Watsonx Orchestrate
  fields:
    - label: Download F1 Race Feed Tool
      source: spec
      value: "https://small-underpass-refinery.ngrok-free.dev/watsonx/f1-race-feed-openapi.json"
```

The existing Apps Script turns values beginning with `http` into links in the
claim email. Prefer this link over Apps Script attachment logic; it keeps WSA
workshop-neutral and avoids Drive file mapping.

Email instructions:

1. Download the F1 Race Feed Tool file.
2. In Watsonx, choose **Add tool -> OpenAPI**.
3. Upload the JSON file.
4. Select only **Get the live race feed**.
5. Add it to the agent. No connection or authentication is required.

Don't tell attendees to use MCP server, Streamable HTTP, SSE, or direct RTCE.

## Phase 6: tests

Add automated coverage for:

- The Watsonx spec contains one operation and no parameters.
- The hardcoded path is `/race-feed/f1wp050`.
- `servers[0].url` matches the configured public URL.
- `/healthz` doesn't appear in the attendee spec.
- The response switches to the newest `race_id` and rejects delayed old races.
- Replayed retained records don't set `live=true`.
- Missing or invalid account-50 credentials fail before the server starts.
- Logs and OpenAPI output contain no credential values.
- The account-50 preparation command can't touch another manifest account.
- Resetting account 50 leaves it stopped and returns it to baseline.

Run the focused tests, then the full Python suite, Ruff, Terraform validation,
targeted Terraform plans, and `git diff --check`. Follow the repository's
verify-before-done checklist before handoff.

## Phase 7: live acceptance

Use account 50 and a separate attendee-style Watsonx instance.

1. Run cohort preparation; confirm every account is stopped.
2. Prepare Lab 3/4 only on account 50.
3. Start account 50 and verify a new `race_id` within 60 seconds.
4. Start the feed and ngrok endpoint.
5. Download the public OpenAPI file and upload it to Watsonx.
6. Confirm the imported tool has no prefix input.
7. Call the tool at several laps and verify the lap and standings change.
8. Verify `car_state` appears after anomaly warm-up.
9. Verify a grounded pit decision appears when Lab 4 produces one.
10. Confirm ngrok records `Watson Orchestrate` requests with HTTP 200.
11. Run a 50-client burst test against the same endpoint.
12. Confirm accounts 1-49 have unchanged ECS services, topics, and statements.
13. Stop ngrok and the feed process.
14. Reset account 50 and confirm it is stopped and clean.

Free ngrok limits should cover the expected request count and transfer volume.
The risk to watch is the 100-new-connections-per-minute limit during a synchronized
exercise. Stagger the first tool call across two minutes and attach or pre-download
the OpenAPI file if the rehearsal gets close to that limit.

## Failure and rollback

- Feed unavailable: agents report that the shared race feed is offline; Labs 1-4
  continue unaffected.
- ngrok limit or network failure: restart the same endpoint from the backup
  organizer machine using the same assigned domain.
- Account 50 stops: restart only account 50 through `start-races --accounts 50`.
- Account 50 data becomes inconsistent: reset only account 50, prepare its lab
  objects again, then restart it.
- Watsonx import fails: verify the attendee chose OpenAPI file upload, not MCP.
- Any isolation check fails: stop the tunnel and account 50. Do not touch the
  remaining cohort.

## Acceptance criteria

The work is ready when:

- One emailed JSON file imports into a separate Watsonx instance.
- The imported tool asks for no input.
- Fifty agents can call one public endpoint without credentials.
- Responses change as account 50 progresses through the race.
- The anomaly and pit-decision fields appear when their statements produce data.
- No secret reaches Watsonx, ngrok logs, Git, fixtures, or email.
- Account-50 reset returns it to the same stopped baseline as the cohort.
- Accounts 1-49 remain unchanged throughout the rehearsal.
