-- Run this after both RTCE topics are ACTIVE. It intentionally writes new
-- records after enablement so the materializer has post-enable data to ingest.

INSERT INTO `rtce_standings_delete_test`
VALUES (88, 'John Doe', 'River Racing', 31, 8, 19.2, 1.4, 92.781, 0, 'SOFT', 31, FALSE, TIMESTAMP '2026-08-04 16:00:00.000');

INSERT INTO `rtce_standings_raw_compact_test`
VALUES ('88', 88, 'John Doe', 'River Racing', 31, 8, 19.2, 1.4, 92.781, 0, 'SOFT', 31, FALSE, TIMESTAMP '2026-08-04 16:00:00.000');
