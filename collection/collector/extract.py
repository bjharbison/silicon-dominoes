"""Silicon Dominoes — Step 4a: candidate-event extraction.

Reads un-examined captures from the raw archive, applies a deterministic
facet prefilter, and sends survivors to the local LLM for structured
extraction. Output is a *candidate* event written to `review_queue`.

NOTHING SCORES WITHOUT HUMAN APPROVAL (ARCHITECTURE.md §6). This module
never writes to `events`.

Design notes worth keeping:

* `raw_captures` is immutable — `parse_status` cannot be updated to record
  that a capture was examined. Extraction state therefore lives in
  `review_queue`: EVERY examined capture gets a row, including prefilter
  misses (status='rejected', rejection_reason='prefilter: ...'). This makes
  the gate auditable per system prompt §2.3 — a reader can ask why any
  given capture never produced an event — and makes the run resumable by
  simple anti-join.

* Historical content-hash duplicates cannot be deleted (immutability
  trigger), so work selection uses DISTINCT ON (feed_id, url) with the
  earliest capture_id winning.

* The LLM never assigns `source_tier`. Tier is a property of the
  publication, recorded per feed in feeds.yaml, and is attached here from
  config — not inferred from prose.

* Reasoning blocks are stripped before JSON parsing (homelab principle),
  and temperature is 0 for reproducibility.

Run:  python -m collector.extract [--limit N] [--dry-run] [--capture-id N]
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import traceback
from typing import Any

import requests

from . import common, config

# ---------------------------------------------------------------- constants --
# Mirrors the Postgres enums exactly. A candidate whose value is not in these
# sets is rejected before it reaches the queue, so a typo'd layer name can
# never fail an insert at approval time.
LAYERS = {"power", "facilities", "silicon", "networks", "cloud", "models",
          "applications"}
DIRECTIONS = {"us", "prc", "sovereign", "reversal"}
POLES = {"us", "prc", "sovereign", "other"}
DEPTH_DIMS = {"D", "E", "P", "T"}
P_SUBINDICATORS = {"p_license", "p_update", "p_spares", "p_jurisdiction",
                   "p_telemetry"}

PILOT_COUNTRIES = {"IDN", "VNM", "THA", "MYS", "PHL", "SGP", "KHM", "LAO", "MMR"}

MAX_CHARS = 12000       # article text sent to the model; long enough for any
                        # trade-press piece, short enough to stay fast

SYSTEM_PROMPT = """You are an evidence-coding assistant for an open-source \
intelligence project measuring which AI technology stack each country is \
embedded in. You convert news articles into structured candidate events for \
human review.

You are cautious and literal. You extract only what the article states. You \
never infer, never estimate, and never fill a gap with a plausible value. \
Missing information is null.

Return ONLY a JSON object. No prose, no markdown fences, no explanation.

Schema:
{
  "relevant": true | false,
  "reason_if_irrelevant": string | null,
  "events": [
    {
      "country_iso3": one of IDN VNM THA MYS PHL SGP KHM LAO MMR,
      "event_date": "YYYY-MM-DD" or null (the date the thing HAPPENED, not
                    the publication date, if they differ and both are given),
      "layer": one of power facilities silicon networks cloud models applications,
      "instrument_tier": integer 1-5,
      "direction": one of us prc sovereign reversal,
      "reversal_target": one of us prc sovereign other, or null
                         (REQUIRED only when direction is "reversal"),
      "depth_dimensions": array from D E P T,
      "p_subindicators": array from p_license p_update p_spares p_jurisdiction
                         p_telemetry, or null,
      "summary": one or two sentences IN YOUR OWN WORDS. Never copy sentences
                 from the article.
      "controller_name": the entity that would hold leverage (vendor, creditor,
                         cloud operator), or null,
      "sub_state_actor_name": the ministry, state telco, sovereign fund, or
                              province actually making the decision, or null,
      "announced_value_usd": number or null (announced/headline figure),
      "verified_value_usd": number or null (ONLY if the article cites a signed,
                            filed, or closed figure — otherwise null),
      "bundle_hint": short string if this looks like part of a multi-layer
                     package, else null,
      "contested_decision": true if the article reports disagreement WITHIN a
                            government over this procurement, else false,
      "confidence": number 0.0-1.0, your confidence in this coding
    }
  ]
}

Instrument tier ladder — assign by what has ACTUALLY happened:
  1  statement of intent, communique, non-binding MOU, letter of intent
  2  framework agreement, feasibility study, pilot, proof of concept
  3  signed commercial contract or binding procurement award
     (requires a stated value AND a named counterparty; if either is
      missing, code 2 and say so in the summary)
  4  financing closed OR construction started OR export license issued.
     A signed loan, credit facility, green loan, or financial close is
     tier 4 even when no construction has begun — the money moving is
     the commitment. Do not code these as tier 3.
  5  operational at scale, migration completed, refresh contracted
  Cancellations, bans, expulsions, license denials, forced divestments are
  direction="reversal" at the tier of the thing being reversed.

Rules:
- STOCK VS FLOW. This is the most common coding error, so read it twice:
  * T (Trajectory) = a NEW commitment being made. Contracts, loans, awards,
    MOUs, groundbreakings, planned capacity. Anything not yet in service.
  * D (Dependence) = installed base that ALREADY EXISTS and is in service.
  * E (Exit cost) = contract tenor, debt secured against the asset, workforce
    certification, data portability — the things that make leaving costly.
  * P (Provenance) = who can see, deny, or license the thing.
  A financing announcement for a facility completing in 2027 is ["T"], NOT
  ["D"]. Only code D when the article states something is operational,
  deployed, or in service now. When a deal creates long-term debt or a long
  tenor, include "E" alongside "T".
- LAYER. The physical building is not the service running in it:
  * facilities = the data center itself — campus, land, MW IT load, cooling,
    colocation, construction, the financing of any of these.
  * cloud = a cloud region, availability zone, sovereign cloud, or migration
    to a named cloud provider.
  * power = generation, PPAs, grid connection, substations.
  * silicon = chips and accelerators as procured hardware.
  A data center campus, and any loan or land deal for one, is ALWAYS
  facilities — even when it is described as serving AI or cloud workloads.
- controller_name is REQUIRED whenever the article names a vendor, creditor,
  lender, or operator who would hold leverage. For a loan, that is the lender
  or the bank consortium. Only null if genuinely nobody is named.
- event_date: if the article gives no explicit date for the event, use the
  publication date rather than null.
- Only the nine pilot countries above. An article about Germany or India is
  irrelevant even if it names a vendor on your watchlist.
- One event per country per layer. A package spanning layers becomes several
  events sharing a bundle_hint.
- direction reflects WHOSE STACK the commitment moves the country toward, not
  who is speaking. A Chinese vendor losing a contract to a US vendor is
  direction "us".
- "sovereign" covers EU, Gulf, Japan, Korea, India, Israel, Singapore, Brazil,
  and open-weight substitution — a distinct pole, never a residual.
- If the article is about a vendor's global earnings, a product launch with no
  country-specific deployment, market forecasts, opinion, or an event outside
  the pilot scope, set relevant=false and return an empty events array.
- Do NOT assign source tiers. That is not your job.
"""


# ------------------------------------------------------------------- facets --
def load_facets() -> dict:
    return config.load_facet("entities")


def _flatten(node: Any, out: list[str]) -> None:
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, list):
        for item in node:
            _flatten(item, out)
    elif isinstance(node, dict):
        for value in node.values():
            _flatten(value, out)


def build_terms(facets: dict) -> tuple[dict[str, list[str]], list[str]]:
    """Returns (geo_terms_by_iso3, entity_terms)."""
    geo = {iso3: list(terms) for iso3, terms in facets.get("geo", {}).items()}
    entities: list[str] = []
    _flatten(facets.get("actors", {}), entities)
    _flatten(facets.get("operators", []), entities)
    return geo, entities


_MATCHERS: dict[str, Any] = {}


def _matches(term: str, text: str) -> bool:
    """Word-boundary match. Substring matching produced false positives that
    would have wasted LLM calls and polluted the queue: "Digi" inside
    "digital", "DICT" inside "predict", "Intel" inside "intelligence", "GIC"
    inside "strategic". Short all-caps terms are additionally matched
    case-sensitively, since "TIME" and "AIS" are common English fragments
    while the organisations are not."""
    rx = _MATCHERS.get(term)
    if rx is None:
        flags = 0 if (term.isupper() and len(term) <= 5) else re.IGNORECASE
        rx = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", flags)
        _MATCHERS[term] = rx
    return rx.search(text) is not None


def prefilter(text: str, geo: dict[str, list[str]],
              entities: list[str]) -> dict[str, Any]:
    """Deterministic gate: >=1 pilot geo term AND >=1 watchlist entity.

    Auditable by construction — the matched terms are recorded on the queue
    row, so any decision to skip a capture can be inspected later.
    """
    geo_hits = {iso3: [t for t in terms if _matches(t, text)]
                for iso3, terms in geo.items()}
    geo_hits = {k: v for k, v in geo_hits.items() if v}
    entity_hits = [e for e in entities if _matches(e, text)]
    return {
        "geo_hits": geo_hits,
        "entity_hits": entity_hits[:20],
        "passed": bool(geo_hits) and bool(entity_hits),
    }


# ------------------------------------------------------------------ archive --
def read_capture(object_key: str) -> tuple[str, str] | None:
    """Returns (kind, text) where kind is 'html' or 'json', or None if the
    object is missing or unreadable. Handles the pre-2026-08-16 uncompressed
    objects and the gzipped ones written since."""
    path = config.ARCHIVE_DIR / object_key
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
        if object_key.endswith(".gz"):
            raw = gzip.decompress(raw)
    except OSError:
        return None

    if ".json" in object_key:
        return "json", raw.decode("utf-8", errors="replace")

    try:
        import trafilatura
        text = trafilatura.extract(raw.decode("utf-8", errors="replace"),
                                   include_comments=False,
                                   include_tables=False)
    except Exception:
        text = None
    if not text:
        return None
    return "html", text


# ---------------------------------------------------------------------- LLM --
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


def strip_reasoning(text: str) -> str:
    """Remove reasoning blocks and markdown fences before JSON parsing."""
    text = _THINK_RE.sub("", text)
    text = _FENCE_RE.sub("", text)
    return text.strip()


def call_llm(article_text: str, url: str) -> dict | None:
    user = (f"Source URL: {url}\n\n"
            f"Article text:\n\n{article_text[:MAX_CHARS]}")
    headers = {"Content-Type": "application/json"}
    if config.LLM_KEY:
        headers["Authorization"] = f"Bearer {config.LLM_KEY}"
    try:
        resp = requests.post(
            f"{config.LLM_BASE}/v1/chat/completions",
            headers=headers,
            json={
                "model": config.LLM_MODEL,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
            },
            timeout=config.LLM_TIMEOUT,
        )
    except requests.RequestException as exc:
        print(f"    LLM request failed: {exc}")
        return None
    if not resp.ok:
        print(f"    LLM HTTP {resp.status_code}: {resp.text[:200]}")
        return None
    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        print(f"    LLM response malformed: {exc}")
        return None

    cleaned = strip_reasoning(content)
    # The model occasionally emits leading prose despite instruction; take the
    # outermost JSON object rather than failing the whole capture.
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        print(f"    no JSON object in response: {cleaned[:160]}")
        return None
    try:
        return json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as exc:
        print(f"    JSON parse failed: {exc}")
        return None


# --------------------------------------------------------------- validation --
def validate_event(ev: dict) -> tuple[dict | None, list[str]]:
    """Coerce and check one extracted event. Returns (event, problems).
    Problems are recorded on the queue row rather than silently dropped —
    a model that keeps inventing layer names is a fact the reviewer should
    be able to see."""
    problems: list[str] = []

    iso3 = str(ev.get("country_iso3") or "").upper()
    if iso3 not in PILOT_COUNTRIES:
        return None, [f"country_iso3 {iso3!r} outside pilot scope"]

    layer = str(ev.get("layer") or "").lower()
    if layer not in LAYERS:
        return None, [f"layer {layer!r} not a stack_layer"]

    direction = str(ev.get("direction") or "").lower()
    if direction not in DIRECTIONS:
        return None, [f"direction {direction!r} not an event_direction"]

    try:
        tier = int(ev.get("instrument_tier"))
    except (TypeError, ValueError):
        return None, ["instrument_tier missing or non-numeric"]
    if not 1 <= tier <= 5:
        return None, [f"instrument_tier {tier} out of range"]

    reversal_target = ev.get("reversal_target")
    if direction == "reversal":
        rt = str(reversal_target or "").lower()
        if rt not in POLES:
            problems.append("reversal without valid reversal_target")
            reversal_target = None
        else:
            reversal_target = rt
    else:
        reversal_target = None

    dims = [d.upper() for d in (ev.get("depth_dimensions") or [])
            if str(d).upper() in DEPTH_DIMS]
    if not dims:
        dims = ["T"]        # a new commitment always evidences Trajectory
        problems.append("depth_dimensions empty; defaulted to [T]")

    subs = [s.lower() for s in (ev.get("p_subindicators") or [])
            if str(s).lower() in P_SUBINDICATORS] or None

    summary = str(ev.get("summary") or "").strip()
    if not summary:
        return None, ["summary empty"]

    try:
        conf = float(ev.get("confidence"))
    except (TypeError, ValueError):
        conf = 0.30
        problems.append("confidence missing; defaulted to 0.30")
    conf = max(0.0, min(1.0, conf))

    def _num(key: str) -> float | None:
        val = ev.get(key)
        if val in (None, "", "null"):
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            problems.append(f"{key} non-numeric; nulled")
            return None

    # Goodhart guard, applied at extraction: an unverified announcement never
    # arrives carrying a verified value.
    verified = _num("verified_value_usd")
    if verified is not None and tier < 3:
        problems.append("verified_value_usd on a tier-1/2 event; moved to announced")
        verified = None

    return {
        "country_iso3": iso3,
        "event_date": ev.get("event_date") or None,
        "layer": layer,
        "instrument_tier": tier,
        "direction": direction,
        "reversal_target": reversal_target,
        "depth_dimensions": dims,
        "p_subindicators": subs,
        "summary": summary,
        "controller_name": ev.get("controller_name") or None,
        "sub_state_actor_name": ev.get("sub_state_actor_name") or None,
        "announced_value_usd": _num("announced_value_usd"),
        "verified_value_usd": verified,
        "bundle_hint": ev.get("bundle_hint") or None,
        "contested_decision": bool(ev.get("contested_decision")),
        "confidence": round(conf, 2),
        "analyst_inference": False,
    }, problems


# ------------------------------------------------------------------ database --
SELECT_WORK = """
SELECT DISTINCT ON (rc.feed_id, rc.url)
       rc.capture_id, rc.feed_id, rc.url, rc.object_key, rc.retrieved_at
  FROM raw_captures rc
 WHERE NOT EXISTS (SELECT 1 FROM review_queue rq
                     JOIN raw_captures seen ON seen.capture_id = rq.capture_id
                    WHERE seen.feed_id = rc.feed_id AND seen.url = rc.url)
 ORDER BY rc.feed_id, rc.url, rc.capture_id
"""


def fetch_work(conn, limit: int | None, capture_id: int | None) -> list[dict]:
    sql, params = SELECT_WORK, []
    if capture_id is not None:
        sql = ("SELECT capture_id, feed_id, url, object_key, retrieved_at "
               "FROM raw_captures WHERE capture_id = %s")
        params = [capture_id]
    elif limit:
        sql += " LIMIT %s"
        params = [limit]
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def enqueue(conn, capture_id: int, candidate: dict, status: str,
            rejection_reason: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO review_queue (capture_id, candidate, status, rejection_reason)
            VALUES (%s, %s, %s, %s)
            """,
            (capture_id, json.dumps(candidate, ensure_ascii=False), status, rejection_reason),
        )
    conn.commit()


# ---------------------------------------------------------------------- main --
def process(conn, row: dict, geo, entities, source_tiers: dict[str, str],
            dry_run: bool) -> str:
    """Returns a one-word outcome for the run summary."""
    capture_id, url = row["capture_id"], row["url"]
    obj = read_capture(row["object_key"])
    if obj is None:
        if not dry_run:
            enqueue(conn, capture_id,
                    {"extractor": "unreadable", "object_key": row["object_key"]},
                    "rejected", "archive object missing or text extraction failed")
        return "unreadable"

    kind, text = obj
    if kind == "json":
        # GDELT payloads are result lists, not articles; they are handled by a
        # separate path (not yet built) that expands them into per-article
        # fetches. Recording the skip keeps the queue an honest census.
        if not dry_run:
            enqueue(conn, capture_id, {"extractor": "skipped", "kind": "json"},
                    "rejected", "GDELT result payload — not an article")
        return "skipped-json"

    pf = prefilter(text, geo, entities)
    if not pf["passed"]:
        if not dry_run:
            enqueue(conn, capture_id, {"extractor": "prefilter", "prefilter": pf},
                    "rejected", "prefilter: no pilot geo + watchlist entity match")
        return "filtered"

    print(f"  [{capture_id}] {url}")
    print(f"    geo={list(pf['geo_hits'])} entities={pf['entity_hits'][:5]}")
    if dry_run:
        return "would-extract"

    result = call_llm(text, url)
    if result is None:
        enqueue(conn, capture_id, {"extractor": "llm_failed", "prefilter": pf},
                "needs_more_sourcing", "LLM extraction failed — retry")
        return "llm-failed"

    if not result.get("relevant") or not result.get("events"):
        enqueue(conn, capture_id,
                {"extractor": "llm", "prefilter": pf,
                 "reason": result.get("reason_if_irrelevant")},
                "rejected", "LLM judged not relevant to pilot scope")
        return "not-relevant"

    source_tier = source_tiers.get(row["feed_id"])
    kept = 0
    for raw_ev in result["events"]:
        event, problems = validate_event(raw_ev)
        if event is None:
            enqueue(conn, capture_id,
                    {"extractor": "llm", "prefilter": pf, "raw": raw_ev,
                     "problems": problems},
                    "rejected", "; ".join(problems)[:400])
            continue
        candidate = {
            "extractor": "llm",
            "model": config.LLM_MODEL,
            "event": event,
            "source": {
                "url": url,
                "feed_id": row["feed_id"],
                "source_tier": source_tier,     # from feeds.yaml, never inferred
                "retrieved_at": row["retrieved_at"].isoformat(),
            },
            "prefilter": pf,
            "problems": problems,
        }
        enqueue(conn, capture_id, candidate, "pending")
        kept += 1
        flag = f"  ⚠ {len(problems)} problem(s)" if problems else ""
        print(f"    → {event['country_iso3']} / {event['layer']} / "
              f"T{event['instrument_tier']} / {event['direction']}{flag}")
    return "extracted" if kept else "rejected"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None,
                    help="process at most N captures")
    ap.add_argument("--capture-id", type=int, default=None,
                    help="process one specific capture (ignores prior queue rows)")
    ap.add_argument("--dry-run", action="store_true",
                    help="prefilter only; no LLM calls, no database writes")
    args = ap.parse_args()

    facets = load_facets()
    geo, entities = build_terms(facets)
    print(f"facets v{facets.get('version')}: "
          f"{sum(len(v) for v in geo.values())} geo terms, "
          f"{len(entities)} entity terms")

    cfg = config.load_feeds_config()
    source_tiers = {f["feed_id"]: f.get("source_tier")
                    for f in cfg.get("feeds", [])}
    missing = [fid for fid, tier in source_tiers.items() if not tier]
    if missing:
        print(f"WARNING: no source_tier in feeds.yaml for: {', '.join(missing)}")

    conn = common.connect()
    work = fetch_work(conn, args.limit, args.capture_id)
    print(f"{len(work)} capture(s) to examine"
          + (" [DRY RUN]" if args.dry_run else ""))

    tally: dict[str, int] = {}
    for row in work:
        try:
            outcome = process(conn, row, geo, entities, source_tiers, args.dry_run)
        except Exception as exc:                # one bad capture never stops a run
            traceback.print_exc()
            outcome = "error"
            if not args.dry_run:
                try:
                    enqueue(conn, row["capture_id"],
                            {"extractor": "error", "detail": str(exc)[:500]},
                            "needs_more_sourcing", f"extractor error: {exc}"[:400])
                except Exception:
                    conn.rollback()
        tally[outcome] = tally.get(outcome, 0) + 1

    print("\nrun summary: " + ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    if tally.get("llm-failed") or tally.get("error"):
        common.notify(
            "Collector: extraction run had failures",
            ", ".join(f"{k}={v}" for k, v in sorted(tally.items())),
            priority="high", tags="warning",
        )
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
