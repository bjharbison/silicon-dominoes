-- Silicon Dominoes — schema addendum 005: gdelt-slow
-- Run after collection/sql/004_feed_runs.sql:
--   Applied by Brian as postgres (tables are postgres-owned); see STATUS.md.
--
-- Two independent additions for collector/poll_gdelt.py (gdelt-slow), found
-- by reading the live schema before writing any code:
--
-- 1. feed_runs.outcome's CHECK constraint (004) only allows 'ok', 'failed',
--    'timeout', 'budget_exhausted', 'skipped_quarantined' — none of which
--    fit a GDELT-specific result. GDELT DOC 2.0's sticky, IP-wide throttle
--    (see collector/breaker.py's gdelt_cooldown_active) needs two more:
--      'throttled'         — the DOC 2.0 response body was classified as a
--                             rate-limit block OR was unparseable (see
--                             collector.fetcher.classify_gdelt_body — an
--                             unparseable body must never be recorded as a
--                             clean empty poll, so both verdicts share this
--                             one outcome; which of the two it actually was
--                             is kept in feed_runs.error, a free-text column
--                             with no CHECK, not here).
--      'skipped_throttled'  — this invocation never issued a request at all
--                             because collector.breaker.gdelt_cooldown_active
--                             found a recent throttle on ANY kind: gdelt
--                             query (mirrors 'skipped_quarantined', which is
--                             poll_rss's per-feed equivalent).
--
-- 2. raw_captures (db/schema.sql) has no JSONB/metadata column at all.
--    poll_gdelt.py needs one to carry per-capture GDELT provenance
--    (query_id, seendate, sourcecountry, language, domain) — fields that
--    exist only on a GDELT DOC 2.0 article result and have nowhere else to
--    live; RSS captures never populate it (common.insert_capture's new
--    `metadata` parameter defaults to NULL for every existing caller).
--
-- No new GRANT statements: db/schema.sql already runs
--   GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA public TO sd_pipeline;
-- which is table-level and therefore already covers raw_captures.metadata
-- (a new column on an already-granted table needs no separate grant in
-- Postgres) and feed_runs' two new CHECK-allowed outcome values (a CHECK
-- constraint is not a privilege — feed_runs' own 004 GRANT SELECT, INSERT
-- already covers inserting a row with either new outcome).

BEGIN;

ALTER TABLE feed_runs DROP CONSTRAINT feed_runs_outcome_check;
ALTER TABLE feed_runs ADD CONSTRAINT feed_runs_outcome_check CHECK (outcome IN
  ('ok', 'failed', 'timeout', 'budget_exhausted', 'skipped_quarantined',
   'throttled', 'skipped_throttled'));

ALTER TABLE raw_captures ADD COLUMN metadata jsonb;

COMMIT;
