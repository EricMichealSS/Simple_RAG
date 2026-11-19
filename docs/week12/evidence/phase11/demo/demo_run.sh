#!/usr/bin/env bash
# The 10-minute, four-part demo as one reproducible script (docs/week12/demo_script.md explains what to say).
# NOT RUN AS A WHOLE: written alongside demo_script.md but never executed end to end (the machine ran out of memory, see phase11.md).
# Run from developer-rag/ with the venv active (or set PY). Needs a built index; Parts 1-2 use the model (about 5 calls, ~9k tokens).
# Every command is timed. Redirect the output to demo_run_output.txt to keep a record (that file does not exist yet).
set -u
PY="${PY:-python}"
FIX="../docs/week12/evidence/phase11/demo/demo_notes.md"
OWNER="demo-$(date +%H%M%S)"
part() { echo; echo "################ $1"; }
run()  { echo; echo "\$ $*"; local s e; s=$(date +%s%N); "$@" 2>&1 | grep -a -v "Loading weights\|HF Hub"; local code=${PIPESTATUS[0]}; e=$(date +%s%N); echo "[exit $code, $(( (e - s) / 100000000 )) tenths of a second]"; }

part "PART 1: the right answer for the right version (about 3 minutes)"
run $PY -W ignore -m sdk_engine versions
run $PY -W ignore -m sdk_engine query "In GitHub API version 2025-06-01, what parameter replaces the deprecated single assignee field when creating an issue?"
run $PY -W ignore -m sdk_engine query "Compare how the context parameter format changed between GitHub API version 2022-11-28 and 2025-06-01 for rendering Markdown."

part "PART 2: it says no, asks, and cannot be talked into anything (about 2.5 minutes)"
run $PY -W ignore -m sdk_engine query "What is the rate limit for unauthenticated GraphQL queries in GitHub API version 2025-06-01?"
run $PY -W ignore -m sdk_engine query "How do I create an issue?"
run $PY -W ignore -m sdk_engine upload "$FIX" --owner "$OWNER"
run $PY -W ignore -m sdk_engine query "In my uploaded notes, what colour was the east fence repainted?" --owner "$OWNER"
run $PY -W ignore -m sdk_engine uploads list --owner "somebody-else-$OWNER"
run $PY -W ignore -m sdk_engine uploads purge --owner "$OWNER" --yes

part "PART 3: find and inspect any request (about 2.5 minutes)"
run $PY -W ignore -m sdk_engine trace find --since 30m --limit 6
TID=$($PY -W ignore -m sdk_engine trace find --since 30m --limit 6 --status answered --json 2>/dev/null | $PY -c "import json,sys; d=json.load(sys.stdin); print(d['requests'][0]['trace_id'])")
echo "chosen trace: $TID"
run $PY -W ignore -m sdk_engine trace show "$TID"
run $PY -W ignore -m sdk_engine health --deep

part "PART 4: the evidence (about 2 minutes)"
run $PY -W ignore -m sdk_engine eval regress
run $PY -W ignore -m sdk_engine eval trajectory --results ../docs/week12/evidence/phase9/golden_results.json --out ../docs/week12/evidence/phase11/demo/trajectory_summary_demo.json
echo; echo "Then open docs/week12/known_weaknesses.md (the top three) and docs/week12/final_review.md (the one unmet item)."
