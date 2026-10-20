#!/usr/bin/env python3
"""Validate showcase country records against SPEC.md §3–4. Exit 1 on any problem.

Usage: python3 showcase/tools/check_country.py showcase/countries/VNM.json [more ...]
"""
import json
import re
import sys
from pathlib import Path

LAYERS = {"power", "facilities", "silicon", "networks", "cloud", "models", "applications"}
GAP_LAYERS = {"power", "facilities", "silicon", "networks", "models", "applications"}
POLES = {"us", "prc", "third", "domestic", None}
TIERS = {"S1", "S2", "S3", "S4"}
DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
# Domains that must never be fetched first-hand, so must never be cited directly either.
BANNED_HOST_RE = re.compile(
    r"(\.cn(/|$|:)|baidu\.com|xinhuanet\.com|news\.cn|people\.com\.cn|cgtn\.com|"
    r"globaltimes\.cn|chinadaily\.com\.cn|alibabacloud\.com|huaweicloud\.com|"
    r"tencentcloud\.com|huawei\.com|zte\.com\.cn|hikvision\.com|dahuasecurity\.com)",
    re.I)
PLACEHOLDER_RE = re.compile(r"(example\.(com|org)|\.invalid|localhost|REPLACE)", re.I)
FIELD_ORDER = ["schema", "spec_version", "iso3", "name", "review_status", "coded_at",
               "coded_by", "findings", "layers_searched", "research_gaps", "searches_run"]


def check(path):
    p = []
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        return [f"not valid JSON: {e}"]
    m = re.search(r"([A-Z]{3})\.json$", str(path))
    if not m or rec.get("iso3") != m.group(1):
        p.append("iso3 must match the file name")
    if list(rec.keys()) != FIELD_ORDER:
        p.append(f"top-level keys must be exactly, in order: {FIELD_ORDER}")
    if rec.get("schema") != "sd-showcase-country/0.1":
        p.append("schema must be sd-showcase-country/0.1")
    if rec.get("review_status") != "unreviewed":
        p.append("review_status must be 'unreviewed'")
    if not isinstance(rec.get("searches_run"), int):
        p.append("searches_run must be an integer")
    ids = set()
    for i, f in enumerate(rec.get("findings") or []):
        tag = f"findings[{i}]"
        fid = f.get("id", "")
        if not re.fullmatch(rf"{rec.get('iso3')}-\d{{3}}", str(fid)) or fid in ids:
            p.append(f"{tag}: id must be unique <ISO3>-NNN")
        ids.add(fid)
        if f.get("layer") not in LAYERS:
            p.append(f"{tag}: bad layer {f.get('layer')!r}")
        if f.get("pole", "missing") not in POLES:
            p.append(f"{tag}: bad pole {f.get('pole')!r}")
        if f.get("pole") is None and not f.get("analyst_inference"):
            p.append(f"{tag}: pole null needs analyst_inference saying why")
        if f.get("instrument_tier") not in (1, 2, 3, 4, 5):
            p.append(f"{tag}: instrument_tier must be 1-5")
        if not isinstance(f.get("reversal"), bool):
            p.append(f"{tag}: reversal must be true/false")
        if f.get("reversal") and not f.get("reversal_of"):
            p.append(f"{tag}: reversal needs reversal_of")
        if f.get("date") is not None and not DATE_RE.match(str(f.get("date"))):
            p.append(f"{tag}: date must be YYYY, YYYY-MM or YYYY-MM-DD")
        s = f.get("summary") or ""
        if not s or len(s.split()) > 40:
            p.append(f"{tag}: summary required, at most 40 words")
        if f.get("confidence") not in ("high", "medium", "low"):
            p.append(f"{tag}: confidence must be high/medium/low")
        srcs = f.get("sources") or []
        if not srcs:
            p.append(f"{tag}: at least one source required")
        for j, src in enumerate(srcs):
            url = src.get("url") or ""
            if not url.startswith("https://") and not url.startswith("http://"):
                p.append(f"{tag}.sources[{j}]: url missing")
            if BANNED_HOST_RE.search(url.split("?")[0]):
                p.append(f"{tag}.sources[{j}]: {url} is a PRC-hosted or vendor site; cite secondary reporting")
            if PLACEHOLDER_RE.search(url):
                p.append(f"{tag}.sources[{j}]: placeholder url")
            if src.get("source_tier") not in TIERS:
                p.append(f"{tag}.sources[{j}]: source_tier must be S1-S4 (S5 is never cited)")
        # Corroboration rule, recomputed rather than trusted.
        good = [x for x in srcs if x.get("source_tier") in ("S1", "S2", "S3")]
        hosts = {re.sub(r"^www\.", "", (x.get("url") or "").split("/")[2]) for x in good
                 if (x.get("url") or "").count("/") >= 2}
        corroborated = len(hosts) >= 2 and any(x.get("source_tier") in ("S1", "S2") for x in good)
        if f.get("corroborated") is True and not corroborated:
            p.append(f"{tag}: marked corroborated but lacks 2 independent S1-S3 sources incl. one S1/S2")
        if f.get("instrument_tier", 0) >= 3 and not corroborated and f.get("confidence") != "low":
            p.append(f"{tag}: uncorroborated tier>=3 finding must have confidence 'low'")
    ls = rec.get("layers_searched") or {}
    if set(ls) != GAP_LAYERS:
        p.append(f"layers_searched must list exactly {sorted(GAP_LAYERS)}")
    coded_layers = {f.get("layer") for f in rec.get("findings") or []}
    for layer, v in ls.items():
        r = (v or {}).get("result")
        if r not in ("coded", "null", "not_searched"):
            p.append(f"layers_searched.{layer}: result must be coded/null/not_searched")
        if r in ("null", "not_searched") and not v.get("null_reason"):
            p.append(f"layers_searched.{layer}: {r} needs null_reason")
        if r == "coded" and layer not in coded_layers:
            p.append(f"layers_searched.{layer}: says coded but no finding has that layer")
    return p


def main():
    bad = 0
    for path in sys.argv[1:]:
        problems = check(path)
        if problems:
            bad += 1
            print(f"FAIL {path}")
            for x in problems:
                print("   -", x)
        else:
            print(f"ok   {path}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
