"""Retry Wayback snapshots for captures that missed one at capture time.

raw_captures is APPEND-ONLY (db/schema.sql), so a snapshot obtained after
the capture row exists cannot be written back to it. Late snapshots append
to url_snapshots (db/sql/002_url_snapshots.sql) instead; the
captures_with_snapshots view coalesces both.

Run:  python -m collector.snapshot_retry
"""
from __future__ import annotations

import sys

from . import common

BATCH = 25


def main() -> int:
    conn = common.connect()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.capture_id, c.url
            FROM raw_captures c
            LEFT JOIN url_snapshots s ON s.capture_id = c.capture_id
            WHERE c.snapshot_id IS NULL
              AND s.capture_id IS NULL
              AND c.url NOT LIKE %s
            ORDER BY c.retrieved_at DESC
            LIMIT %s
            """,
            ("https://api.gdeltproject.org%", BATCH),
        )
        pending = cur.fetchall()

    if not pending:
        print("no captures awaiting snapshots")
        conn.close()
        return 0

    done = 0
    for capture_id, url in pending:
        snap_id, snap_url = common.wayback_submit(url)
        if snap_id:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO url_snapshots (capture_id, snapshot_id, snapshot_url) "
                    "VALUES (%s, %s, %s)",
                    (capture_id, snap_id, snap_url))
            conn.commit()
            done += 1
            print(f"[{capture_id}] snapshotted -> {snap_url}")
        else:
            print(f"[{capture_id}] snapshot failed, will retry next run")

    print(f"done: {done}/{len(pending)} snapshotted")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
