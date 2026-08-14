-- Silicon Dominoes — schema addendum 002: late URL snapshots
-- Run after db/schema.sql:  psql "$DB_URL" -v ON_ERROR_STOP=1 -f 002_url_snapshots.sql
--
-- Why this exists: raw_captures is append-only, so a Wayback snapshot that
-- succeeds AFTER the capture row was inserted cannot be written back to it.
-- Late snapshots append here; captures_with_snapshots coalesces both paths.

BEGIN;

CREATE TABLE url_snapshots (
  snap_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  capture_id    bigint NOT NULL REFERENCES raw_captures(capture_id),
  snapshot_id   text NOT NULL,
  snapshot_url  text NOT NULL,
  snapshotted_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_url_snapshots_capture ON url_snapshots (capture_id);
CALL make_append_only('url_snapshots');

CREATE VIEW captures_with_snapshots AS
SELECT c.*,
       coalesce(c.snapshot_id,  s.snapshot_id)  AS effective_snapshot_id,
       coalesce(c.snapshot_url, s.snapshot_url) AS effective_snapshot_url
FROM raw_captures c
LEFT JOIN LATERAL (
  SELECT snapshot_id, snapshot_url FROM url_snapshots
  WHERE capture_id = c.capture_id ORDER BY snapshotted_at LIMIT 1
) s ON true;

GRANT SELECT, INSERT ON url_snapshots TO sd_pipeline;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO sd_pipeline;
GRANT SELECT ON captures_with_snapshots TO sd_pipeline;

COMMIT;
