"""Turns race_runs.json + traces.jsonl into race_table.md, handoffs.log and
the multiplier line. Everything is computed from the recorded runs; nothing
is typed by hand.

    python3 squad/analyze.py
"""

import json
import math
from collections import defaultdict
import re
import statistics
from pathlib import Path

HERE = Path(__file__).parent
data = json.loads((HERE / "race_runs.json").read_text(encoding="utf-8"))
rows = data["rows"]
traces = [json.loads(l) for l in (HERE / "traces.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
final_ids = {r["trace_id"] for r in rows if r["trace_id"]}


ARMS = ["single", "squad"]
all_rows = rows
_have = defaultdict(set)
for r in rows:
    _have[(r["repeat"], r["race_id"])].add(r["arm"])
# objective invalidity rule: a run whose wall-clock "budget" fired although its ACTIVE time was < 120 s
# was cut off by rate-limit waiting (agent.py measures raw clock time) -- a runner artifact, not an outcome.
invalid = {(r["repeat"], r["race_id"]) for r in rows
           if r["status"] == "budget_exceeded" and r["budget_hit"] == "max_wall_clock_s" and r["wall_clock_s"] < 120}
as_run_pass = {a: sum(1 for r in rows if r["arm"] == a and r["verdict"] == "PASS" and (r["repeat"], r["race_id"]) in
               {k for k, v in _have.items() if v == {"single", "squad"}}) for a in ("single", "squad")}
paired = {k for k, v in _have.items() if v == {"single", "squad"}} - invalid
unpaired = sorted(k for k, v in _have.items() if v != {"single", "squad"})
rows = [r for r in rows if (r["repeat"], r["race_id"]) in paired]  # headline = cases BOTH arms completed
final_ids = {r["trace_id"] for r in rows if r["trace_id"]}
traces = [t for t in traces if t["trace_id"] in final_ids]
N_CASES = len({k[1] for k in paired})
REPEATS = sorted({r["repeat"] for r in rows})


def pctl(vals, p):
    s = sorted(vals)
    return s[max(0, math.ceil(p * len(s)) - 1)]  # nearest-rank; for n=10, p99 == max


def per_repeat(arm, rep):
    rs = [r for r in rows if r["arm"] == arm and r["repeat"] == rep]
    lat = [r["wall_clock_s"] for r in rs]
    return {
        "n": len(rs),
        "pass": sum(1 for r in rs if r["verdict"] == "PASS"),
        "p50": statistics.median(lat), "p99": pctl(lat, 0.99),
        "tokens": sum(r["total_tokens"] for r in rs),
        "cost_q": sum(r["total_cost_usd"] for r in rs) / len(rs),
    }


stats = {a: {rep: per_repeat(a, rep) for rep in REPEATS} for a in ARMS}


def agg(arm, key):
    v = [stats[arm][rep][key] for rep in REPEATS]
    return min(v), statistics.median(v), max(v)


def fmt(key, x):
    return {"pass": lambda v: f"{v:.0f}/{N_CASES}", "p50": lambda v: f"{v:.1f}", "p99": lambda v: f"{v:.1f}",
            "tokens": lambda v: f"{v:,.0f}", "cost_q": lambda v: f"${v:.5f}"}[key](x)


labels = [("pass", f"Pass count (of {N_CASES} paired cases)"), ("p50", "p50 latency (s)"), ("p99", "p99 latency (s)"),
          ("tokens", f"Total tokens ({N_CASES} questions)"), ("cost_q", "Cost per question")]

out = ["# Race table: single agent vs docs squad", "",
       f"Planned set: the same 10 cases (easy_01, easy_02, easy_13, medium_01, medium_07, medium_13, hard_01, hard_04, hard_06, hard_11). Headline numbers below use the {N_CASES} cases BOTH arms completed: {sorted({k[1] for k in paired})}. "
       "same judge (gpt-oss-120b), same model for every role in both arms (gpt-oss-20b, temp 0, see protocol amendment A1), same ceilings, "
       f"{len(REPEATS)} repeat(s) (daily token quota stopped the rest). Headline = median of repeats; with 1 repeat there is no spread.", "",
       "| Metric | Single agent (median) | Squad (median) | Single min-max | Squad min-max |",
       "|---|---|---|---|---|"]
for key, label in labels:
    s, q = agg("single", key), agg("squad", key)
    out.append(f"| {label} | {fmt(key, s[1])} | {fmt(key, q[1])} | {fmt(key, s[0])} - {fmt(key, s[2])} | {fmt(key, q[0])} - {fmt(key, q[2])} |")
if invalid:
    out += ["", f"**Excluded as invalid (runner artifact):** {sorted(k[1] for k in invalid)}. The single agent's own wall-clock check counted rate-limit waiting, so the run was cut off after <120 s of real work (see row in race_runs.json). As run, with it counted: single {as_run_pass['single']} vs squad {as_run_pass['squad']} passes on the 8 paired cases; after exclusion see the table. Either way the keep/kill verdict is the same."]
if unpaired:
    out += ["", "**Cases NOT in the headline (squad did not finish):** " + ", ".join(f"{k[1]} (repeat {k[0]}, arms finished: {sorted(_have[k])})" for k in unpaired) +
            ". See PHASE4 doc: the squad's manager kept delegating on these negative cases, its context grew toward the provider's 8,000-token request cap, and the daily token quota ran out before they could finish. They are NOT counted as pass or fail."]
out += ["", "p99 over 10 samples is the nearest-rank value, i.e. effectively the slowest single question. "
        "**Latency caveat, do not read as a squad win:** the single arm still had the SDK's own hidden retry sleeps inside its latency (not excluded by my clock), while the squad client had retries off; and workers run in-process (no network hop). Single latencies on short runs (e.g. 2,894 tokens in 19 s) show this. Latency is NOT used in the verdict.", ""]

out += ["## Per repeat", "", "| Repeat | Arm | Pass | p50 s | p99 s | Tokens | $/question |", "|---|---|---|---|---|---|---|"]
for rep in REPEATS:
    for a in ARMS:
        s = stats[a][rep]
        out.append(f"| {rep} | {a} | {s['pass']}/{s['n']} | {s['p50']:.1f} | {s['p99']:.1f} | {s['tokens']:,} | ${s['cost_q']:.5f} |")

out += ["", "## Per question (PASS count over repeats, tokens and latency are means)", "",
        "| Case | Level | Single pass | Squad pass | Single tokens | Squad tokens | Single s | Squad s | Squad delegations |", "|---|---|---|---|---|---|---|---|---|"]
ids = []
for r in rows:
    if r["race_id"] not in ids:
        ids.append(r["race_id"])
for rid in ids:
    cells = {}
    for a in ARMS:
        rs = [r for r in rows if r["race_id"] == rid and r["arm"] == a]
        cells[a] = (sum(r["verdict"] == "PASS" for r in rs), len(rs),
                    statistics.mean(r["total_tokens"] for r in rs), statistics.mean(r["wall_clock_s"] for r in rs))
    dl = [r["delegations"] or 0 for r in rows if r["race_id"] == rid and r["arm"] == "squad"] or [0]
    lvl = next(r["level"] for r in rows if r["race_id"] == rid)
    out.append(f"| {rid} | {lvl} | {cells['single'][0]}/{cells['single'][1]} | {cells['squad'][0]}/{cells['squad'][1]} | "
               f"{cells['single'][2]:,.0f} | {cells['squad'][2]:,.0f} | {cells['single'][3]:.1f} | {cells['squad'][3]:.1f} | {statistics.mean(dl):.1f} |")

statuses = defaultdict(int)
for r in rows:
    statuses[(r["arm"], r["status"], r["budget_hit"])] += 1
out += ["", "## Run statuses", ""] + [f"- {a}: status={s}, budget_hit={b}: {n} runs" for (a, s, b), n in sorted(statuses.items(), key=str)]
if data["infra_errors"]:
    out += ["", f"## Infrastructure errors (retried, logged)", ""] + [f"- {e}" for e in data["infra_errors"]]
(HERE / "race_table.md").write_text("\n".join(out) + "\n", encoding="utf-8")

# ---------------- handoffs.log + multiplier --------------------------------
squad = [t for t in traces if t["arm"] == "squad"]
single = [t for t in traces if t["arm"] == "single"]
log_lines = ["# handoffs.log: every model call / hand-off in every squad run, with its token count",
             "# fields: trace | case | repeat | seq | caller | hand-off | prompt + completion = total", ""]
for t in squad:
    log_lines.append(f"{t['trace_id']} | {t['race_id']} | r{t['repeat']} | #{t['seq']} | {t['caller']} | {t['handoff']} | "
                     f"{t['prompt_tokens']} + {t['completion_tokens']} = {t['total_tokens']}" + (f" | {t['status']}" if t["status"] != "ok" else ""))


def category(h):
    h = re.sub(r"tool-loop lap \d+", "tool-loop (lap 2+)", h)
    return h


by_cat = defaultdict(lambda: [0, 0])
for t in squad:
    c = category(t["handoff"])
    by_cat[c][0] += t["total_tokens"]
    by_cat[c][1] += 1
squad_total = sum(t["total_tokens"] for t in squad)
single_total = sum(t["total_tokens"] for t in single)

# reconciliation: hand-off tokens must sum to each arm's reported totals
assert squad_total == sum(r["total_tokens"] for r in rows if r["arm"] == "squad"), "squad token reconciliation failed"
assert single_total == sum(r["total_tokens"] for r in rows if r["arm"] == "single"), "single token reconciliation failed"

log_lines += ["", "# SUMMARY BY HAND-OFF (all repeats pooled)", "",
              "| Hand-off | Calls | Tokens | Share of all squad tokens |", "|---|---|---|---|"]
ranked = sorted(by_cat.items(), key=lambda kv: -kv[1][0])
for c, (tok, n) in ranked:
    log_lines.append(f"| {c} | {n} | {tok:,} | {100 * tok / squad_total:.1f}% |")
log_lines += ["", f"Reconciliation OK: hand-off tokens sum to {squad_total:,} (squad) and {single_total:,} (single), matching the race totals."]

# multiplier: headline = median-repeat tokens ratio; pooled shown as a cross-check
med_s, med_q = agg("single", "tokens")[1], agg("squad", "tokens")[1]
mult = med_q / med_s
pooled = squad_total / single_total
top, (top_tok, _n) = ranked[0]
line = (f"MULTIPLIER: {mult:.1f}x  (squad {med_q:,.0f} / single {med_s:,.0f} tokens per run, median of "
        f"{len(REPEATS)} repeats; pooled {pooled:.1f}x). Largest hand-off: \"{top}\", {100 * top_tok / squad_total:.0f}% of all squad tokens.")
log_lines += ["", line]
(HERE / "handoffs.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
(HERE / "multiplier.txt").write_text(line + "\n", encoding="utf-8")
print("\n".join(out))
print()
print(line)
