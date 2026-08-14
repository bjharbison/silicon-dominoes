"""Poll GDELT DOC 2.0 with a faceted Boolean query (system prompt §3.1–3.2).

The query is a faceted product — (ACTORS) AND (DOMAINS) AND (GEO) — built at
runtime from the versioned facet files. Synonyms are never hard-coded here.
Each run archives the raw JSON result set as one capture (deduped by content
hash, so an unchanged result set is not re-stored).

Run:  python -m collector.poll_gdelt
"""
from __future__ import annotations

import sys

import requests

from . import common, config


def or_group(terms: list[str], cap: int) -> str:
    quoted = [f'"{t}"' if " " in t else t for t in terms[:cap]]
    return "(" + " OR ".join(quoted) + ")"


def build_query(gdelt_cfg: dict) -> str:
    cap = int(gdelt_cfg.get("max_terms_per_facet", 8))
    groups = [or_group(config.load_facet(name)["terms"], cap)
              for name in gdelt_cfg["facets"]]
    return " ".join(groups)          # space = AND in GDELT query syntax


def main() -> int:
    cfg = config.load_feeds_config()
    conn = common.connect()
    config.sync_feeds(conn, cfg)

    ran = 0
    for feed in cfg.get("feeds", []):
        if feed["feed_class"] != "structured_news":
            continue
        g = feed["gdelt"]
        query = build_query(g)
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": int(g.get("maxrecords", 100)),
            "timespan": g.get("timespan", "1d"),
            "sort": "datedesc",
        }
        print(f"[{feed['feed_id']}] query: {query}")
        try:
            resp = requests.get(feed["url"], params=params,
                                headers={"User-Agent": config.USER_AGENT},
                                timeout=60)
            resp.raise_for_status()
            new = common.insert_capture(
                conn, feed_id=feed["feed_id"], url=resp.url,
                payload=resp.content, ext="json", parse_status="captured")
            print(f"[{feed['feed_id']}] {'new result set archived' if new else 'unchanged result set'}")
            ran += 1
        except requests.RequestException as exc:
            common.notify("Collector: GDELT poll failed",
                          f"{feed['feed_id']}: {exc}", priority="high", tags="warning")
    conn.close()
    return 0 if ran else 1


if __name__ == "__main__":
    sys.exit(main())
