#!/usr/bin/env bash
# The ONLY way automated runs push. Runs the guard, then a plain (never forced) push of
# the showcase branch to origin/showcase. Any failure stops here with nothing pushed.
#   usage: bash showcase/tools/safe_push.sh daily|build
set -euo pipefail
mode="${1:?usage: safe_push.sh daily|build}"
cd "$(git rev-parse --show-toplevel)"
python3 showcase/tools/guard.py --mode "$mode"
# Explicit refspec: local showcase -> remote showcase, nothing else. No --force, no --tags,
# no --delete, no --mirror, ever.
git push origin refs/heads/showcase:refs/heads/showcase
echo "pushed: $(git rev-parse --short HEAD) -> origin/showcase"
