-- Run this only after the first RTCE lookup has returned the baseline row.
-- The record key is unchanged; every changed field is intentionally obvious.

INSERT INTO `rtce_standings_delete_test`
VALUES (88, 'John Doe', 'River Racing', 32, 7, 15.8, 0.6, 91.904, 0, 'MEDIUM', 0, FALSE, TIMESTAMP '2026-08-04 16:01:00.000');

INSERT INTO `rtce_standings_raw_compact_test`
VALUES ('88', 88, 'John Doe', 'River Racing', 32, 7, 15.8, 0.6, 91.904, 0, 'MEDIUM', 0, FALSE, TIMESTAMP '2026-08-04 16:01:00.000');
