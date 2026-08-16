-- URL dedup index (STATUS.md, 2026-08-16 second session).
-- Deliberately NON-UNIQUE: historical duplicate rows predate the poller-side
-- dedup fix and cannot be deleted (trg_raw_captures_immutable blocks DELETE,
-- and that trigger is a T-guarantee). A UNIQUE index would therefore fail to
-- build. Uniqueness is enforced in poll_rss.poll_feed via
-- common.url_already_captured(), not in the schema.
CREATE INDEX IF NOT EXISTS idx_raw_captures_url ON raw_captures (feed_id, url);
