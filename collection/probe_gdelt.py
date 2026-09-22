"""Hand-run GDELT DOC 2.0 probe (gdelt-slow) — collects REAL evidence for
collector.fetcher.GDELT_THROTTLE_SIGNATURES, currently provisional guesses
against GDELT's documented (but unconfirmed-in-practice) rate-limit wording.

NOT wired to any timer, NOT imported by collector/poll_gdelt.py or any other
collector module — standalone and hand-run only. Run it by hand, sparingly:
issuing more than a handful of requests here defeats the entire point of
gdelt-slow (one query per timer fire) and risks triggering the very block
this project exists to characterize and avoid.

OPERATING RULE: every new kind: gdelt query gets exactly one hand-run probe
here (--query, below) before it is ever enabled in feeds.yaml. This is not
optional — an unknown/text-error response from a query GDELT doesn't like
the shape of costs a 24h GLOBAL cooldown (collector/breaker.py's gdelt_
cooldown_active blocks every kind: gdelt query, not just the bad one) plus
a high-priority alert every rotation until it clears, for the entire
duration. Finding that out from the fixed probes/feeds.yaml's own next
scheduled run is a far more expensive way to learn it than one hand-run
--query check first.

NEVER run this probe (fixed or --query) while feed_runs has a 'throttled'
row in the last 24h — it shares the same public IP as the timer-driven
poller, so a probe run during an active cooldown either (a) also gets
throttled, learning nothing new, or (b) if it somehow doesn't, still counts
against however GDELT is actually rate-limiting, extending whatever is
already wrong. Check feed_health/the ledger first.

Issues AT MOST 4 requests, each at least 15 seconds apart, strictly serial
(no concurrency, no retries), through collector.fetcher — the same bounded
HTTP path every other collector module uses. Every response body is saved
to disk verbatim, alongside its status code, headers, and elapsed time, so
the throttle signatures in collector/fetcher.py can be tightened from a
real captured example instead of documentation guesswork. Stops
immediately (non-zero exit) on the first throttled/unknown verdict — it
never continues probing into an active block, on the theory that if this
one probe run trips it, running a fifth "just to check" only makes the
sticky, IP-wide condition (collector/breaker.py's gdelt_cooldown_active)
worse for every other query, including the ordinary timer-driven ones.

Run by hand:
  python collection/probe_gdelt.py /path/to/output/dir
  python collection/probe_gdelt.py --query 'TEXT' /path/to/output/dir
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # .../collection

from collector import config, fetcher  # noqa: E402

# GDELT DOC 2.0's public article-list endpoint — kept as a constant here
# (not imported from collector.poll_gdelt) so this probe stays fully
# standalone and never depends on the timer-driven module's internals.
GDELT_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"

MIN_GAP_S = 15.0
MAX_REQUESTS = 4

# Every request — every fixed probe below AND a --query one-off — uses the
# same fixed params, only `query` itself varies. One place to change them.
FIXED_PARAMS = {"mode": "artlist", "format": "json", "timespan": "7d",
                "maxrecords": "10", "sort": "datedesc"}


def _params_for(query: str) -> dict:
    return {"query": query, **FIXED_PARAMS}


# (label, params, critical) — three deliberate probes, in an order chosen
# so a rejection never stops the run before something useful is learned.
# `critical` controls whether a non-ok verdict stops the whole run (see
# main()'s loop):
#   1. '"data center" sourcecountry:VM' — a multi-word phrase certain to
#      match real, ordinary Vietnamese-tech coverage — proves a normal
#      'ok' response with real articles, on the FIRST request, with no
#      term short enough to risk a rejection. CRITICAL.
#   2. a deliberately nonsensical query expected to match NOTHING — proves
#      an empty 'articles' list ('ok', zero results) is distinguishable
#      from a throttle/unknown verdict in a REAL response, not just in the
#      unit tests' synthetic bodies — i.e. what zero results actually
#      looks like on the wire. CRITICAL.
#   3. "5G sourcecountry:VM sourcelang:vietnamese" — LAST, on purpose: "5G"
#      is a 2-character term, and GDELT is documented to reject terms
#      under 3 characters, so this is the one probe whose rejection is an
#      EXPECTED POSSIBLE outcome, not evidence of a throttle — every prior
#      probe having already proven 'ok'/'throttled' are both reachable and
#      distinguishable means a non-ok verdict HERE is informative on its
#      own (answers "does GDELT actually enforce the 3-char minimum?")
#      without risking that answer costing the whole run. Also exercises
#      sourcelang, a second query-syntax feature a future feeds.yaml entry
#      might rely on. NOT CRITICAL: never stops the run or changes the
#      exit code, only saved for inspection like every other response.
PROBE_QUERIES = [
    ("vnm_datacenter_phrase", _params_for('"data center" sourcecountry:VM'), True),
    ("nonsense_no_match",
     _params_for("xyzzy1701nonexistentquerystring9999 sourcecountry:VM"), True),
    ("vnm_5g_vietnamese_lang_may_be_rejected",
     _params_for("5G sourcecountry:VM sourcelang:vietnamese"), False),
]


def _save(output_dir: Path, index: int, label: str, *, query: str, status_code: int,
         headers: dict, body: bytes, elapsed: float) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{index:02d}_{label}"
    (output_dir / f"{stem}.body").write_bytes(body)
    manifest = {
        "label": label,
        "query": query,
        "status_code": status_code,
        "headers": dict(headers),
        "elapsed_s": elapsed,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    (output_dir / f"{stem}.meta.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def _issue_one(params: dict) -> tuple["fetcher.FetchResult", float]:
    start = time.monotonic()
    resp = fetcher.get(GDELT_ENDPOINT, params=params,
                       headers={"User-Agent": config.USER_AGENT})
    elapsed = time.monotonic() - start
    return resp, elapsed


def _run_one_adhoc(query: str, output_dir: Path) -> int:
    """--query TEXT: exactly ONE request for a literal, caller-supplied
    query — same FIXED_PARAMS every probe uses — saved and reported the
    same way as a fixed probe, but never touching PROBE_QUERIES at all.
    This is the "one hand-run probe before enabling any new kind: gdelt
    query" the module docstring's OPERATING RULE requires."""
    params = _params_for(query)
    print(f"[1/1] adhoc: {query!r}")
    try:
        resp, elapsed = _issue_one(params)
    except fetcher.FetchError as exc:
        print(f"[1/1] FETCH ERROR: {exc}")
        return 1

    _save(output_dir, 1, "adhoc", query=query, status_code=resp.status_code,
         headers=resp.headers, body=resp.content, elapsed=elapsed)

    verdict = fetcher.classify_gdelt_body(resp.status_code, resp.content)
    preview = resp.content[:200]
    print(f"[1/1] status={resp.status_code} elapsed={elapsed:.2f}s verdict={verdict}")
    print(f"[1/1] body[:200]={preview!r}")

    if verdict in ("throttled", "unknown"):
        print(f"[1/1] verdict={verdict} — this query is NOT safe to enable in "
             "feeds.yaml as written; see the saved body before retrying")
        return 1
    print(f"done: 1 request issued, verdict=ok, saved under {output_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("output_dir", type=Path,
                    help="directory to save each response body + metadata into")
    ap.add_argument("--query", type=str, default=None,
                    help="issue exactly ONE request for this literal query text "
                         "instead of running PROBE_QUERIES — the required pre-check "
                         "before enabling any new kind: gdelt feeds.yaml entry")
    args = ap.parse_args(argv)

    if args.query is not None:
        return _run_one_adhoc(args.query, args.output_dir)

    if len(PROBE_QUERIES) > MAX_REQUESTS:
        print(f"REFUSING to run: {len(PROBE_QUERIES)} probe queries exceeds "
             f"the MAX_REQUESTS={MAX_REQUESTS} cap — this is a bug in this "
             "file, fix PROBE_QUERIES before running")
        return 1

    last_request_at: float | None = None
    for index, (label, params, critical) in enumerate(PROBE_QUERIES, start=1):
        if last_request_at is not None:
            wait = MIN_GAP_S - (time.monotonic() - last_request_at)
            if wait > 0:
                print(f"waiting {wait:.1f}s before the next request "
                     f"(minimum {MIN_GAP_S:g}s between requests)...")
                time.sleep(wait)

        print(f"[{index}/{len(PROBE_QUERIES)}] {label}: {params['query']!r}")
        try:
            resp, elapsed = _issue_one(params)
        except fetcher.FetchError as exc:
            print(f"[{index}/{len(PROBE_QUERIES)}] FETCH ERROR: {exc}")
            return 1
        last_request_at = time.monotonic()

        _save(args.output_dir, index, label, query=params["query"],
             status_code=resp.status_code, headers=resp.headers,
             body=resp.content, elapsed=elapsed)

        verdict = fetcher.classify_gdelt_body(resp.status_code, resp.content)
        preview = resp.content[:200]
        print(f"[{index}/{len(PROBE_QUERIES)}] status={resp.status_code} "
             f"elapsed={elapsed:.2f}s verdict={verdict}")
        print(f"[{index}/{len(PROBE_QUERIES)}] body[:200]={preview!r}")

        if verdict in ("throttled", "unknown"):
            if critical:
                print(f"[{index}/{len(PROBE_QUERIES)}] STOPPING — verdict={verdict}, "
                     "never continuing into a possible block")
                return 1
            print(f"[{index}/{len(PROBE_QUERIES)}] non-ok verdict={verdict} — "
                 "saved for inspection - expected possible (not a critical probe, "
                 "does not stop the run or change the exit code)")

    print(f"done: {len(PROBE_QUERIES)} request(s) issued, every CRITICAL probe "
         f"classified 'ok' (a non-ok verdict on the last, NOT CRITICAL probe, if "
         f"any, was expected-possible and did not fail this run), "
         f"saved under {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
