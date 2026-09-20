-- Silicon Dominoes — schema addendum 004: feed run ledger
-- Run after collection/sql/003_url_index.sql:
--   Applied by Brian as postgres (tables are postgres-owned); see STATUS.md.
--
-- Why this exists: feed_health.py (ARCHITECTURE.md §4) reads only
-- raw_captures, so "this feed had nothing new" and "the collector never ran"
-- are indistinguishable from that table alone. On 2026-09-19 sd-rss hung 18
-- hours on one feed and produced no signal that would have distinguished it
-- from a quiet night. feed_runs records every poll ATTEMPT — one row per
-- feed per run, success or failure, including runs a feed never actually
-- reached because it was skipped — so collector/breaker.py can contain a
-- persistently bad feed, and feed_health.py (a later task) can tell "quiet
-- feed" from "the poller stopped calling this feed" from "dead collector."
--
-- Append-only, same pattern as raw_captures / url_snapshots: a correction is
-- a new row, never an edit. Owned by postgres like every other table; the
-- service role holds SELECT/INSERT only (CLAUDE.md §1 — DDL is applied by
-- hand, never by the pipeline).

BEGIN;

CREATE TABLE feed_runs (
  run_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  feed_id       text NOT NULL REFERENCES feeds(feed_id),
  started_at    timestamptz NOT NULL,
  finished_at   timestamptz NOT NULL,
  outcome       text NOT NULL CHECK (outcome IN
                  ('ok', 'failed', 'timeout', 'budget_exhausted', 'skipped_quarantined')),
  entries_seen  integer NOT NULL DEFAULT 0,
  new_captures  integer NOT NULL DEFAULT 0,
  error         text
);
CREATE INDEX idx_feed_runs_feed_finished ON feed_runs (feed_id, finished_at DESC);
CALL make_append_only('feed_runs');

-- Grantee is sd_pipeline, the role every other collector table grants
-- to (verified live 2026-09-20; dominoes is a member of it). No sequence
-- grant: run_id is an identity column, which needs no privilege on its
-- underlying sequence, and a schema-wide sequence grant is out of scope.
GRANT SELECT, INSERT ON feed_runs TO sd_pipeline;

COMMIT;
