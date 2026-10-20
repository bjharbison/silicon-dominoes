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
     The global layer files, the spec, the map and these tools can only change in a
     session Brian is watching (--mode build).

Usage:  python3 showcase/tools/guard.py --mode daily|build
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["daily", "build"], required=True)
    mode = ap.parse_args().mode
    problems = []

    branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch != "showcase":
        problems.append(f"current branch is '{branch}', not 'showcase'")

    if git("status", "--porcelain").strip():
        problems.append("working tree has uncommitted changes; commit or discard them first")

    git("fetch", "--quiet", "origin", "main", "showcase")
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
