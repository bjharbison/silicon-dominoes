#!/usr/bin/env python3
"""
Silicon Dominoes — contract validation (pipeline step 6 / CI gate).

Usage:
    python validate.py <artifact_dir>      # validate a publication cycle
    python validate.py --self-test         # validate the bundled fixtures

A contract that fails validation does not publish (ARCHITECTURE.md §7.6).

Four validation layers run here:

  1. JSON Schema validation (draft 2020-12) of each artifact against its
     schema, with common.schema.json resolved locally.
  2. Containment check (CLAUDE.md §1): any envelope carrying synthetic:true
     or provisional:true fails, by name, independent of whatever the schema
     does or does not catch via unevaluatedProperties. Demo and desk-pass
     datasets are structurally unpublishable and this must keep saying so
     even if a schema is loosened later.
  3. Cross-field checks that JSON Schema cannot express. These implement
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

     Note: no check here enforces exposure_us + exposure_prc +
     exposure_sovereign against any total (doc 07 F-7 rule 5) — no
     exposure field is ever a residual of the others.
  4. Doc 07 F-2/F-7 controller-registry checks, named and hard-failing:
     check_controller_resolution, check_pole_derivation,
     check_direction_derived, check_consortium_members,
     check_reversal_target (all gated on schema validity, like the
     cross-field checks above, since they assume schema-valid shapes),
     and check_no_fixture_artifacts (ungated — a publish-path gate that
     runs on every real cycle directory, i.e. everything outside
     schemas/fixtures/; fixtures/ is expected to contain the markers it
     looks for).

`--self-test` validates fixtures/ (must pass) and every case directory
under fixtures/must-reject/ (must each fail).

Requires: jsonschema >= 4.18  (pip install jsonschema)
"""

import json
import re
import sys
from pathlib import Path

EPS = 0.01
SCHEMA_DIR = Path(__file__).parent
FIXTURES_DIR = SCHEMA_DIR / "fixtures"

CONTRACTS = {
    "countries.json": "countries.schema.json",
    "edges.json": "edges.schema.json",
    "events.json": "events.schema.json",
    "ledger.json": "ledger.schema.json",
    "controllers.json": "controllers.schema.json",
}

POLES = ("us", "prc", "third")

# self_test() calls check_no_fixture_artifacts directly (bypassing the
# fixtures/ exemption) against this one case, to demonstrate the publish-path
# gate without contradicting it for every other case living under fixtures/.
FIXTURE_URL_LEAK_CASE = "fixture-url-leak"


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
            errors.append(f"{artifact}: MISSING — all five JSON contracts must publish together")
            continue
        validator = make_validator(schema_file)
        for err in sorted(validator.iter_errors(load(path)), key=lambda e: e.json_path):
            errors.append(f"{artifact}: {err.json_path}: {err.message}")


def containment_check(artifact_dir: Path, errors: list[str]):
    """CLAUDE.md §1: synthetic:true or provisional:true must fail validation
    with its own named error, on every contract that carries the envelope —
    never proceed on the assumption that unevaluatedProperties will always
    be the thing that catches it."""
    for artifact in CONTRACTS:
        path = artifact_dir / artifact
        if not path.exists():
            continue
        data = load(path)
        if not isinstance(data, dict):
            continue
        if data.get("synthetic") is True:
            errors.append(
                f"{artifact}: CONTAINMENT — envelope carries synthetic: true; "
                f"synthetic/demo datasets are structurally unpublishable (CLAUDE.md §1)"
            )
        if data.get("provisional") is True:
            errors.append(
                f"{artifact}: CONTAINMENT — envelope carries provisional: true; "
                f"provisional/desk-pass datasets are structurally unpublishable (CLAUDE.md §1)"
            )


def _load_optional(artifact_dir: Path, name: str):
    path = artifact_dir / name
    return load(path) if path.exists() else None


def _pole_map():
    return load(SCHEMA_DIR / "pole_map.json")


def _effective_pole(controller: dict, pole_map: dict) -> str:
    override = controller.get("pole_override")
    if override is not None:
        return override
    jmap = pole_map.get("map", {})
    return jmap.get(controller.get("control_jurisdiction"), pole_map.get("default"))


def check_controller_resolution(artifact_dir: Path, errors: list[str]):
    """Doc 07 §4: every edge/event controller_id, every parent_id, member
    controller_id, counterparty_id and actor_id resolves to controllers.json."""
    controllers = _load_optional(artifact_dir, "controllers.json")
    if controllers is None:
        return  # schema_validate already reports controllers.json as MISSING
    ids = {c["controller_id"] for c in controllers["controllers"]}

    edges = _load_optional(artifact_dir, "edges.json")
    if edges:
        for e in edges["exposure_edges"]:
            cid = e.get("controller_id")
            if cid is not None and cid not in ids:
                errors.append(
                    f"edges.json: {e['edge_id']}: [check_controller_resolution] "
                    f"controller_id {cid!r} does not resolve to controllers.json"
                )

    events = _load_optional(artifact_dir, "events.json")
    if events:
        for ev in events["events"]:
            cid = ev.get("controller_id")
            if cid is not None and cid not in ids:
                errors.append(
                    f"events.json: {ev['event_id']}: [check_controller_resolution] "
                    f"controller_id {cid!r} does not resolve to controllers.json"
                )
            for cp in ev.get("counterparty_ids", []):
                if cp not in ids:
                    errors.append(
                        f"events.json: {ev['event_id']}: [check_controller_resolution] "
                        f"counterparty_id {cp!r} does not resolve to controllers.json"
                    )
            aid = ev.get("actor_id")
            if aid is not None and aid not in ids:
                errors.append(
                    f"events.json: {ev['event_id']}: [check_controller_resolution] "
                    f"actor_id {aid!r} does not resolve to controllers.json"
                )
            ro = ev.get("reversal_of")
            if isinstance(ro, dict) and "controller_id" in ro and ro["controller_id"] not in ids:
                errors.append(
                    f"events.json: {ev['event_id']}: [check_controller_resolution] "
                    f"reversal_of.controller_id {ro['controller_id']!r} does not resolve to controllers.json"
                )

    for c in controllers["controllers"]:
        pid = c.get("parent_id")
        if pid is not None and pid not in ids:
            errors.append(
                f"controllers.json: {c['controller_id']}: [check_controller_resolution] "
                f"parent_id {pid!r} does not resolve to controllers.json"
            )
        for m in c.get("members", []):
            if m["controller_id"] not in ids:
                errors.append(
                    f"controllers.json: {c['controller_id']}: [check_controller_resolution] "
                    f"member controller_id {m['controller_id']!r} does not resolve to controllers.json"
                )


def check_pole_derivation(artifact_dir: Path, errors: list[str]):
    """Doc 07 F-7 rule 2: effective_pole = pole_override if present else
    pole_map[control_jurisdiction] else pole_map.default. An override
    requires a non-empty override_rationale. Hard fail."""
    controllers = _load_optional(artifact_dir, "controllers.json")
    if controllers is None:
        return
    pole_map = _pole_map()
    jmap = pole_map.get("map", {})
    default = pole_map.get("default")
    if default not in POLES:
        errors.append(f"pole_map.json: [check_pole_derivation] default {default!r} not in {POLES}")
    for jur, p in jmap.items():
        if p not in POLES:
            errors.append(f"pole_map.json: [check_pole_derivation] map[{jur}]={p!r} not in {POLES}")

    for c in controllers["controllers"]:
        override = c.get("pole_override")
        if override is not None:
            if not c.get("override_rationale", "").strip():
                errors.append(
                    f"controllers.json: {c['controller_id']}: [check_pole_derivation] "
                    f"pole_override {override!r} present without a non-empty override_rationale"
                )
        else:
            effective = jmap.get(c.get("control_jurisdiction"), default)
            if effective not in POLES:
                errors.append(
                    f"controllers.json: {c['controller_id']}: [check_pole_derivation] "
                    f"derived pole {effective!r} (from control_jurisdiction {c.get('control_jurisdiction')!r}) "
                    f"not in {POLES}"
                )


def check_direction_derived(artifact_dir: Path, errors: list[str]):
    """Doc 07 F-7 rule 3: where direction_derived is present, its value is
    'sovereign' iff controller.control_jurisdiction == edge.country_iso3,
    else the controller's effective pole. Hard fail on a mismatch."""
    controllers = _load_optional(artifact_dir, "controllers.json")
    edges = _load_optional(artifact_dir, "edges.json")
    if controllers is None or edges is None:
        return
    pole_map = _pole_map()
    by_id = {c["controller_id"]: c for c in controllers["controllers"]}

    for e in edges["exposure_edges"]:
        dd = e.get("direction_derived")
        if dd is None:
            continue
        c = by_id.get(e.get("controller_id"))
        if c is None:
            continue  # unresolved controller_id already reported by check_controller_resolution
        expected = "sovereign" if c.get("control_jurisdiction") == e.get("country_iso3") else _effective_pole(c, pole_map)
        if dd.get("value") != expected:
            errors.append(
                f"edges.json: {e['edge_id']}: [check_direction_derived] direction_derived.value "
                f"{dd.get('value')!r} != expected {expected!r} for controller {c['controller_id']!r} "
                f"(control_jurisdiction {c.get('control_jurisdiction')!r}, edge country {e.get('country_iso3')!r})"
            )


def check_consortium_members(artifact_dir: Path, errors: list[str]):
    """Doc 07 F-7 rule 6: members[] only on controller_type consortium; if
    any share is present, shares sum to <= 1.0; an edge to a consortium with
    no documented controlling member (share > 0.5) carries mixed_control:
    true. Hard fail."""
    controllers = _load_optional(artifact_dir, "controllers.json")
    if controllers is None:
        return
    by_id = {c["controller_id"]: c for c in controllers["controllers"]}

    for c in controllers["controllers"]:
        members = c.get("members")
        if not members:
            continue
        if c.get("controller_type") != "consortium":
            errors.append(
                f"controllers.json: {c['controller_id']}: [check_consortium_members] "
                f"members present on controller_type {c.get('controller_type')!r}; members are consortium-only"
            )
        shares = [m["share"] for m in members if "share" in m]
        if shares and sum(shares) > 1.0 + EPS:
            errors.append(
                f"controllers.json: {c['controller_id']}: [check_consortium_members] "
                f"member shares sum to {sum(shares):.3f} > 1.0"
            )

    edges = _load_optional(artifact_dir, "edges.json")
    if edges:
        for e in edges["exposure_edges"]:
            c = by_id.get(e.get("controller_id"))
            if c is None or c.get("controller_type") != "consortium":
                continue
            has_controlling_member = any(m.get("share", 0) > 0.5 for m in c.get("members") or [])
            if not has_controlling_member and e.get("mixed_control") is not True:
                errors.append(
                    f"edges.json: {e['edge_id']}: [check_consortium_members] controller "
                    f"{c['controller_id']!r} is a consortium with no documented controlling member "
                    f"(no member share > 0.5); edge must carry mixed_control: true"
                )


def check_reversal_target(artifact_dir: Path, errors: list[str]):
    """Doc 07 F-7 rule 8: every is_reversal:true event carries reversal_of
    as a resolvable event/edge reference or a complete, resolvable inline
    reversal_target. Hard fail."""
    events = _load_optional(artifact_dir, "events.json")
    if events is None:
        return
    edges = _load_optional(artifact_dir, "edges.json")
    controllers = _load_optional(artifact_dir, "controllers.json")

    event_ids = {e["event_id"] for e in events["events"]}
    edge_ids = set()
    if edges:
        edge_ids |= {e["edge_id"] for e in edges["exposure_edges"]}
        edge_ids |= {e["edge_id"] for e in edges["cascade_edges"]}
    controller_ids = {c["controller_id"] for c in controllers["controllers"]} if controllers else None

    for e in events["events"]:
        if not e.get("is_reversal"):
            continue
        ro = e.get("reversal_of")
        if not isinstance(ro, dict):
            errors.append(
                f"events.json: {e['event_id']}: [check_reversal_target] is_reversal:true but "
                f"reversal_of is missing — reversals must never be uncodeable (doc 07 F-7 rule 8)"
            )
        elif "event_id" in ro:
            if ro["event_id"] not in event_ids:
                errors.append(
                    f"events.json: {e['event_id']}: [check_reversal_target] reversal_of.event_id "
                    f"{ro['event_id']!r} does not resolve"
                )
        elif "edge_id" in ro:
            if ro["edge_id"] not in edge_ids:
                errors.append(
                    f"events.json: {e['event_id']}: [check_reversal_target] reversal_of.edge_id "
                    f"{ro['edge_id']!r} does not resolve"
                )
        else:
            if controller_ids is not None and ro.get("controller_id") not in controller_ids:
                errors.append(
                    f"events.json: {e['event_id']}: [check_reversal_target] inline reversal_of."
                    f"controller_id {ro.get('controller_id')!r} does not resolve to controllers.json"
                )


_FIXTURE_INVALID_URL_RE = re.compile(r"https?://[^/\s\"']*\.invalid(?:[:/]|$)")
_FIXTURE_TEST_ID_RE = re.compile(r"^test_")


def _walk_strings(obj, path=""):
    """Yield (dotted path, string value) for every string leaf in a JSON tree."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


def check_no_fixture_artifacts(artifact_dir: Path, errors: list[str]):
    """Doc 07 F-7 rule 9: outside schemas/fixtures/, fail on any URL under
    the .invalid TLD, any id matching ^test_, any name containing
    '(fixture)'. Hard fail on the publish path — a hallucinated reference
    has nowhere to live. The fixtures/ exemption is applied by the caller
    (run_bundle), not here, so self-test can demonstrate this check
    directly against fixture content."""
    for artifact in CONTRACTS:
        path = artifact_dir / artifact
        if not path.exists():
            continue
        data = load(path)
        for field_path, value in _walk_strings(data):
            key = field_path.rsplit(".", 1)[-1].split("[")[0]
            if _FIXTURE_INVALID_URL_RE.search(value):
                errors.append(
                    f"{artifact}: {field_path}: [check_no_fixture_artifacts] fixture-only URL "
                    f"({value!r}) outside schemas/fixtures/ — fixture data leaking into a publish"
                )
            if (key == "id" or key.endswith("_id")) and _FIXTURE_TEST_ID_RE.match(value):
                errors.append(
                    f"{artifact}: {field_path}: [check_no_fixture_artifacts] fixture-only id "
                    f"({value!r}) outside schemas/fixtures/"
                )
            if key == "name" and "(fixture)" in value:
                errors.append(
                    f"{artifact}: {field_path}: [check_no_fixture_artifacts] fixture-only name "
                    f"marker '(fixture)' outside schemas/fixtures/ in {value!r}"
                )


def _is_under_fixtures(path: Path) -> bool:
    try:
        path.resolve().relative_to(FIXTURES_DIR.resolve())
        return True
    except ValueError:
        return False


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


def run_bundle(artifact_dir: Path) -> list[str]:
    """Run all four validation layers against one artifact directory."""
    errors: list[str] = []
    schema_validate(artifact_dir, errors)
    containment_check(artifact_dir, errors)
    if not _is_under_fixtures(artifact_dir):
        check_no_fixture_artifacts(artifact_dir, errors)
    if not errors:  # cross-field and F-2/F-7 checks assume schema-valid shapes
        cross_field_checks(artifact_dir, errors)
        check_controller_resolution(artifact_dir, errors)
        check_pole_derivation(artifact_dir, errors)
        check_direction_derived(artifact_dir, errors)
        check_consortium_members(artifact_dir, errors)
        check_reversal_target(artifact_dir, errors)
    return errors


def self_test() -> int:
    """fixtures/ must validate clean; every case dir under
    fixtures/must-reject/ must fail. Returns a process exit code."""
    failures: list[str] = []

    fixtures_dir = SCHEMA_DIR / "fixtures"
    errors = run_bundle(fixtures_dir)
    if errors:
        failures.append(f"fixtures/: expected PASS, got {len(errors)} violation(s):")
        failures.extend(f"    {e}" for e in errors)

    must_reject_dir = fixtures_dir / "must-reject"
    cases = sorted(p for p in must_reject_dir.iterdir() if p.is_dir()) if must_reject_dir.exists() else []
    if not cases:
        failures.append("fixtures/must-reject/: no case directories found — nothing to self-test")
    for case_dir in cases:
        errors = run_bundle(case_dir)
        if case_dir.name == FIXTURE_URL_LEAK_CASE:
            # check_no_fixture_artifacts is a publish-path gate, exempt for
            # anything under fixtures/ by design (run_bundle above skips it
            # here) — call it directly to demonstrate the gate itself.
            check_no_fixture_artifacts(case_dir, errors)
        if not errors:
            failures.append(f"fixtures/must-reject/{case_dir.name}/: expected FAIL, got OK")

    if failures:
        print(f"SELF-TEST FAIL — {len(failures)} problem(s):\n")
        for f in failures:
            print(f"  ✗ {f}")
        return 1

    print(
        f"OK — self-test passed: fixtures/ validates clean; "
        f"{len(cases)} fixtures/must-reject/ case(s) all failed as required."
    )
    return 0


def main():
    args = sys.argv[1:]
    if args == ["--self-test"]:
        sys.exit(self_test())

    artifact_dir = Path(args[0])
    errors = run_bundle(artifact_dir)

    if errors:
        print(f"FAIL — {len(errors)} violation(s); this cycle does not publish:\n")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    print("OK — all contracts valid; cross-field checks passed.")


if __name__ == "__main__":
    main()
