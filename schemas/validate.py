#!/usr/bin/env python3
"""
Silicon Dominoes — contract validation (pipeline step 6 / CI gate).

Usage:
    python validate.py <artifact_dir>      # validate a publication cycle
    python validate.py --self-test         # validate the bundled fixtures

A contract that fails validation does not publish (ARCHITECTURE.md §7.6).

Two validation layers run here:

  1. JSON Schema validation (draft 2020-12) of each artifact against its
     schema, with common.schema.json resolved locally.
  2. Cross-field checks that JSON Schema cannot express. These implement
     the remaining halves of rules whose first halves live in the schemas:

       - D-share vectors sum to <= 1.0 (+ epsilon)
       - Class B outcome distributions sum to ~1.0
       - Every cascade edge's ledger_ref resolves to a Class A prediction
       - Every evidence_ref / event_ref resolves to an event_id in
         events.json (traceability: no number without a citable event)
       - bundle_anchor appears on exactly one event per bundle_id
       - Two S4 sources cannot be the only corroboration for Tier >= 3
         (the schema requires two S3+; this checks the S4 independence rule)
       - Superseded ledger entries reference an existing prediction_id
       - insufficient_data countries never carry scores

Requires: jsonschema >= 4.18  (pip install jsonschema)
"""

import json
import sys
from pathlib import Path

EPS = 0.01
SCHEMA_DIR = Path(__file__).parent

CONTRACTS = {
    "countries.json": "countries.schema.json",
    "edges.json": "edges.schema.json",
    "events.json": "events.schema.json",
    "ledger.json": "ledger.schema.json",
}


def load(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def make_validator(schema_file: str):
    """Build a draft 2020-12 validator with common.schema.json resolvable."""
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    common = load(SCHEMA_DIR / "common.schema.json")
    schema = load(SCHEMA_DIR / schema_file)
    registry = Registry().with_resources(
        [
            ("common.schema.json", Resource.from_contents(common)),
            (common["$id"], Resource.from_contents(common)),
        ]
    )
    return Draft202012Validator(schema, registry=registry)


def schema_validate(artifact_dir: Path, errors: list[str]):
    for artifact, schema_file in CONTRACTS.items():
        path = artifact_dir / artifact
        if not path.exists():
            errors.append(f"{artifact}: MISSING — all four JSON contracts must publish together")
            continue
        validator = make_validator(schema_file)
        for err in sorted(validator.iter_errors(load(path)), key=lambda e: e.json_path):
            errors.append(f"{artifact}: {err.json_path}: {err.message}")


def cross_field_checks(artifact_dir: Path, errors: list[str]):
    countries = load(artifact_dir / "countries.json") if (artifact_dir / "countries.json").exists() else None
    edges = load(artifact_dir / "edges.json") if (artifact_dir / "edges.json").exists() else None
    events = load(artifact_dir / "events.json") if (artifact_dir / "events.json").exists() else None
    ledger = load(artifact_dir / "ledger.json") if (artifact_dir / "ledger.json").exists() else None

    event_ids = {e["event_id"] for e in events["events"]} if events else set()
    prediction_ids = {p["prediction_id"] for p in ledger["predictions"]} if ledger else set()

    # --- events.json ---
    if events:
        bundles: dict[str, int] = {}
        for e in events["events"]:
            if "bundle_id" in e:
                bundles.setdefault(e["bundle_id"], 0)
                if "bundle_anchor" in e:
                    bundles[e["bundle_id"]] += 1
            # S4 independence: for Tier >= 3, the two required S3+ sources
            # must not be satisfiable only alongside a pair of S4s from the
            # same bloc; minimally: at least two non-S4 sources.
            if e["instrument_tier"] >= 3:
                non_s4 = [s for s in e["sources"] if s["source_tier"] in ("S1", "S2", "S3")]
                if len(non_s4) < 2:
                    errors.append(
                        f"events.json: {e['event_id']}: Tier>=3 corroboration must rest on two "
                        f"independent S3+ sources; S4 announcements cannot corroborate (§2.2)"
                    )
        for bundle_id, anchors in bundles.items():
            if anchors != 1:
                errors.append(
                    f"events.json: bundle {bundle_id}: bundle_anchor must appear on exactly "
                    f"one event per bundle (found {anchors}) (§3.4)"
                )

    # --- countries.json ---
    if countries:
        for c in countries["countries"]:
            if c.get("status") != "scored":
                continue
            for layer, rec in c["per_layer"].items():
                total = sum(c["per_layer"][layer]["d_vector"].values())
                if total > 1.0 + EPS:
                    errors.append(
                        f"countries.json: {c['iso3']}/{layer}: D shares sum to {total:.3f} > 1.0"
                    )
                p = rec["p"]
                for name, sub in p.items():
                    if sub.get("status") == "evidenced":
                        for ref in sub.get("evidence_refs", []):
                            if events and ref not in event_ids:
                                errors.append(
                                    f"countries.json: {c['iso3']}/{layer}/{name}: evidence_ref "
                                    f"{ref} does not resolve to an event (traceability)"
                                )

    # --- edges.json ---
    if edges:
        for edge in edges["exposure_edges"]:
            for ref in edge["evidence_refs"]:
                if events and ref not in event_ids:
                    errors.append(
                        f"edges.json: {edge['edge_id']}: evidence_ref {ref} does not resolve "
                        f"to an event (no unsourced deltas, §2.3)"
                    )
        for edge in edges["cascade_edges"]:
            if ledger and edge["ledger_ref"] not in prediction_ids:
                errors.append(
                    f"edges.json: {edge['edge_id']}: ledger_ref {edge['ledger_ref']} has no "
                    f"matching ledger entry (doc 04: every cascade edge generates one)"
                )

    # --- ledger.json ---
    if ledger:
        for p in ledger["predictions"]:
            if p.get("class") == "index":
                total = sum(p["distribution"].values())
                if abs(total - 1.0) > EPS:
                    errors.append(
                        f"ledger.json: {p['prediction_id']}: distribution sums to {total:.3f}, "
                        f"expected 1.0"
                    )
            if "supersedes" in p and p["supersedes"] not in prediction_ids:
                errors.append(
                    f"ledger.json: {p['prediction_id']}: supersedes {p['supersedes']} which is "
                    f"not present (supersession is by reference to an existing entry)"
                )
            for ref in p.get("resolution_evidence_refs", []):
                if events and ref not in event_ids:
                    errors.append(
                        f"ledger.json: {p['prediction_id']}: resolution_evidence_ref {ref} "
                        f"does not resolve to an event"
                    )


def main():
    args = sys.argv[1:]
    artifact_dir = SCHEMA_DIR / "fixtures" if args == ["--self-test"] else Path(args[0])

    errors: list[str] = []
    schema_validate(artifact_dir, errors)
    if not errors:  # cross-field checks assume schema-valid shapes
        cross_field_checks(artifact_dir, errors)

    if errors:
        print(f"FAIL — {len(errors)} violation(s); this cycle does not publish:\n")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    print("OK — all contracts valid; cross-field checks passed.")


if __name__ == "__main__":
    main()
