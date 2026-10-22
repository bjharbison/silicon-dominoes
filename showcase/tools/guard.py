#!/usr/bin/env python3
"""Push guard for the showcase branch. Every push from an automated run must pass this.

Why it exists: the showcase desk pass is written by unattended Claude runs. This script is
the mechanical check that those runs can only ADD showcase material -- never delete
anything, never touch a file outside showcase/, never rewrite a country record that
already exists. It refuses (exit 1) on any violation; safe_push.sh will not push then.

Checks
  1. Current branch is `showcase`.
  2. Nothing anywhere in the repo is deleted or renamed away, relative to where this
     branch left main (merge-base), and relative to what is already on origin/showcase.
  3. Every changed path is under showcase/.
  4. Working tree is clean (everything checked is committed).
  5. --mode daily (used by the scheduled runs) is stricter:
       - since origin/showcase, only showcase/countries/<ISO3>.json files may be ADDED;
         the only files that may be MODIFIED are showcase/queue.json and showcase/RUNLOG.md;
       - at most MAX_NEW_COUNTRIES new country files per push;
       - every new country file must be valid JSON whose iso3 matches its file name and
         whose review_status is "unreviewed".
  6. --mode audit (weekly scheduled audit): may add one showcase/AUDIT-YYYY-MM-DD.md,
     append to RUNLOG.md, and modify existing country records only if each stays valid,
     keeps every finding id, keeps coded_at/coded_by, keeps every earlier corrections entry
     and adds one, is marked "audited", and was not already "reviewed" by Brian.
     The global layer files, the spec, the map and these tools can only change in a
     session Brian is watching (--mode build).

Usage:  python3 showcase/tools/guard.py --mode daily|audit|build
"""
import argparse
import json
import re
import subprocess
import sys

MAX_NEW_COUNTRIES = 15
DAILY_MAY_MODIFY = {"showcase/queue.json", "showcase/RUNLOG.md"}
COUNTRY_RE = re.compile(r"^showcase/countries/([A-Z]{3})\.json$")


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def changes(base):
    """(status, path) pairs from base to HEAD. Renames are split into D + A so a rename
    can never hide a deletion."""
    out = git("diff", "--name-status", "--no-renames", base, "HEAD")
    pairs = []
    for line in out.splitlines():
        if not line.strip():
            continue
        status, path = line.split("\t", 1)
        pairs.append((status[0], path))
    return pairs


AUDIT_REPORT_RE = re.compile(r"^showcase/AUDIT-\d{4}-\d{2}-\d{2}\.md$")
MAX_AUDIT_RECORDS = 120


def audit_check(status, path):
    """--mode audit (weekly scheduled audit). May add one AUDIT-YYYY-MM-DD.md report, append
    to RUNLOG.md, and correct existing country records under strict conditions. Nothing else."""
    p = []
    if status == "A":
        if not AUDIT_REPORT_RE.match(path):
            p.append(f"audit runs may only add an AUDIT-YYYY-MM-DD.md report, not {path}")
        return p
    if path == "showcase/RUNLOG.md":
        return p
    m = COUNTRY_RE.match(path)
    if status != "M" or not m:
        return [f"audit runs may not change {path}"]
    try:
        old = json.loads(git("show", f"origin/showcase:{path}"))
        new = json.loads(git("show", f"HEAD:{path}"))
    except json.JSONDecodeError as e:
        return [f"{path} is not valid JSON: {e}"]
    if old.get("review_status") == "reviewed":
        p.append(f"{path}: Brian has reviewed this record; audit runs may not change it")
    if new.get("iso3") != old.get("iso3") or new.get("iso3") != m.group(1):
        p.append(f"{path}: iso3 changed or does not match the file name")
    if new.get("review_status") != "audited":
        p.append(f"{path}: a corrected record must have review_status 'audited'")
    old_ids = {f.get("id") for f in old.get("findings", [])}
    new_ids = {f.get("id") for f in new.get("findings", [])}
    if not old_ids <= new_ids:
        p.append(f"{path}: removes findings {sorted(old_ids - new_ids)}; audits correct findings, never delete them")
    oc, nc = old.get("corrections") or [], new.get("corrections") or []
    if nc[:len(oc)] != oc or len(nc) <= len(oc):
        p.append(f"{path}: corrections must keep every earlier entry unchanged and add a new one")
    if old.get("coded_at") != new.get("coded_at") or old.get("coded_by") != new.get("coded_by"):
        p.append(f"{path}: coded_at / coded_by must not change")
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["daily", "audit", "build"], required=True)
    mode = ap.parse_args().mode
    problems = []

    branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch != "showcase":
        problems.append(f"current branch is '{branch}', not 'showcase'")

    if git("status", "--porcelain").strip():
        problems.append("working tree has uncommitted changes; commit or discard them first")

    # Explicit refspecs: a shallow single-branch clone has no origin/showcase otherwise.
    # --depth 1000 so merge-base can find the fork point from a shallow clone.
    git("fetch", "--quiet", "--depth=1000", "origin",
        "+refs/heads/main:refs/remotes/origin/main",
        "+refs/heads/showcase:refs/remotes/origin/showcase")
    fork_point = git("merge-base", "origin/main", "HEAD").strip()

    # Whole-branch check against where showcase left main.
    for status, path in changes(fork_point):
        if status == "D":
            problems.append(f"DELETES {path} (relative to main)")
        if not path.startswith("showcase/"):
            problems.append(f"touches {path}, which is outside showcase/")

    # This push only: against what is already published on origin/showcase.
    # HEAD must contain origin/showcase, i.e. no history rewrite.
    if subprocess.run(["git", "merge-base", "--is-ancestor", "origin/showcase", "HEAD"]).returncode != 0:
        problems.append("HEAD does not contain origin/showcase; this would rewrite published history")
    else:
        new_countries = 0
        for status, path in changes("origin/showcase"):
            if status == "D":
                problems.append(f"DELETES {path} (relative to origin/showcase)")
                continue
            if mode == "audit":
                problems.extend(audit_check(status, path))
                continue
            if mode != "daily":
                continue
            m = COUNTRY_RE.match(path)
            if status == "A" and m:
                new_countries += 1
                try:
                    rec = json.loads(git("show", f"HEAD:{path}"))
                except json.JSONDecodeError as e:
                    problems.append(f"{path} is not valid JSON: {e}")
                    continue
                if rec.get("iso3") != m.group(1):
                    problems.append(f"{path}: iso3 field is {rec.get('iso3')!r}, expected {m.group(1)}")
                if rec.get("review_status") != "unreviewed":
                    problems.append(f"{path}: review_status must be 'unreviewed'")
            elif status == "M" and path in DAILY_MAY_MODIFY:
                pass
            elif status == "M" and m:
                problems.append(f"MODIFIES existing country record {path}; daily runs may only add new ones")
            else:
                problems.append(f"daily runs may not {('add' if status == 'A' else 'change')} {path}")
        if mode == "audit":
            n = sum(1 for st, pth in changes("origin/showcase") if st == "M" and COUNTRY_RE.match(pth))
            if n > MAX_AUDIT_RECORDS:
                problems.append(f"{n} records corrected in one push (limit {MAX_AUDIT_RECORDS})")
            bad = subprocess.run(["python3", "showcase/tools/check_country.py"] + [pth for st, pth in changes("origin/showcase") if COUNTRY_RE.match(pth)], capture_output=True, text=True)
            if bad.returncode != 0:
                problems.append("check_country.py fails on a corrected record:\n" + bad.stdout)
        if new_countries > MAX_NEW_COUNTRIES:
            problems.append(f"{new_countries} new country files in one push (limit {MAX_NEW_COUNTRIES})")

    if problems:
        print("GUARD REFUSED -- nothing will be pushed:")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print(f"guard ok ({mode} mode)")


if __name__ == "__main__":
    main()
