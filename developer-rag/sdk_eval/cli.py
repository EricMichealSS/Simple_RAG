"""`python -m sdk_engine eval ...`  (delegated from sdk_engine.cli)

    list                                    the cases, with tier / category / taxonomy modes / origin
    run [--live] [--only ID ...] [--out D]  run the suite (offline tier always; --live adds the real-model cases, paced to the key's limit)
    judge --run D                           grade the live outputs with the judge (secondary signal; never gates a case)
    label-sheet --run D                     write the BLIND sheet + template a human fills in
    agreement --run D --labels FILE         judge vs HUMAN labels (n, agreement, whether >= 85%; "not measured" with no labels)
    regress                                 RED/GREEN for every regression case (defect re-created, then current code)
    golden [--only ID ...] [--between S]    run the 45 golden questions live (trajectory + taxonomy sample; --between = latency run)
    trajectory --results FILE               re-score the 10 trajectory questions from a saved golden run (no model call)
    load [--levels 1,2,4,8] [--requests N]  bounded local load test of the engine's own path (scripted model, nothing sent to a provider)
    history [--dir D]                       one line per saved run, oldest first
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from sdk_engine.config import ENGINE_ROOT

from . import cases as C
from . import judge, regress, runner
from .harness import Env

DEFAULT_RUNS = ENGINE_ROOT / "data" / "eval_runs"


def _cmd_list(args) -> int:
    for s in C.CASES:
        print(f"{s.id:6} {s.tier:7} {s.category:24} {','.join(s.modes):52} {s.origin[:42]:42} {s.title[:62]}")
    cats = {k for s in C.CASES for k in (s.category, *s.covers)}
    print(f"\n{len(C.CASES)} cases ({sum(s.tier == 'offline' for s in C.CASES)} offline, {sum(s.tier == 'live' for s in C.CASES)} live, "
          f"{sum(s.regression for s in C.CASES)} regression); {len(cats)}/{len(C.CATEGORIES)} brief categories covered")
    return 0


def _cmd_run(args) -> int:
    run_dir = Path(args.out) if args.out else runner.new_run_dir(DEFAULT_RUNS)
    env = Env(run_dir / "traces", live=args.live, pace_s=args.pace)
    specs = runner.select(ids=args.only or None, tier=None if args.live else "offline")
    started = datetime.now(timezone.utc).isoformat()
    try:
        results = runner.run_suite(env, specs)
    finally:
        env.close()
    summary = runner.summarize(results)
    meta = {"started": started, "live": env.live, "model": env.cfg.generation_model if env.live else None, "pace_s": args.pace,
            "index_chunks": env.collection.count(), "collection": env.cfg.official_collection}
    runner.save(run_dir, results, summary, meta)
    print("\n" + (run_dir / "report.md").read_text(encoding="utf-8").split("## Cases")[0].strip())
    print(f"status: {summary['status']}  | by mode: " + ", ".join(f"{k} {v['pass']}/{v['denominator']}" for k, v in summary["by_taxonomy_mode"].items()))
    print(f"results written to {run_dir}")
    return 0 if all(r["status"] in ("pass", "skipped") for r in results) else 1


def _run_dir(args) -> Path:
    d = Path(args.run)
    if not (d / "results.json").exists():
        raise SystemExit(f"error: {d} has no results.json (run `eval run` first)")
    return d


def _label_set(d: Path):
    """The items a human labels and the judge grades: the live outputs plus controlled negatives, under opaque ids (see judge.build_label_set)."""
    items_path = d / "labelset_outputs.json"
    if items_path.exists():
        return json.loads(items_path.read_text(encoding="utf-8"))
    return None


def _cmd_judge(args) -> int:
    d = _run_dir(args)
    outputs = _label_set(d) or json.loads((d / "outputs.json").read_text(encoding="utf-8"))
    if not outputs:
        print("no live outputs to judge in this run (run with --live)")
        return 0
    verdicts = judge.judge_outputs(outputs, pace_s=args.pace)
    (d / "judge_verdicts.json").write_text(json.dumps(verdicts, indent=1), encoding="utf-8")
    costs = [v["cost_usd"] for v in verdicts if v["cost_usd"] is not None]
    print(f"{len(verdicts)} verdicts: " + str({k: sum(v['verdict'] == k for v in verdicts) for k in ('PASS', 'FAIL', 'UNPARSED', 'ERROR')})
          + f" | judge cost (reported separately from user-query cost): {round(sum(costs), 6) if costs else 'unavailable'} USD, "
          + f"{sum(v['prompt_tokens'] + v['completion_tokens'] for v in verdicts)} tokens")
    results = json.loads((d / "results.json").read_text(encoding="utf-8"))["results"]
    print("informational (NOT hand labels):", json.dumps({k: v for k, v in judge.judge_vs_deterministic(results, verdicts).items() if k != "disagreements"}))
    key_path = d / "label_key.json"
    if key_path.exists():
        info = judge.judge_on_controlled_items(verdicts, json.loads(key_path.read_text(encoding="utf-8")))
        (d / "judge_on_controlled_items.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
        print("informational (vs MY construction of known-bad answers, NOT hand labels):", json.dumps({k: v for k, v in info.items() if k != "by_kind"}))
    return 0


def _cmd_label_sheet(args) -> int:
    d = _run_dir(args)
    outputs = json.loads((d / "outputs.json").read_text(encoding="utf-8"))
    items, key = judge.build_label_set(outputs)
    (d / "labelset_outputs.json").write_text(json.dumps(items, indent=1), encoding="utf-8")
    (d / "label_key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")        # which items are real vs controlled negatives: do not read before labelling
    (d / "hand_label_sheet.json").write_text(json.dumps(judge.label_sheet(items), indent=1), encoding="utf-8")
    (d / "hand_labels_template.json").write_text(json.dumps(judge.label_template(items), indent=1), encoding="utf-8")
    negs = sum(k["kind"] != "real" for k in key.values())
    print(f"{len(items)} items to label ({len(items) - negs} real outputs + {negs} controlled negatives I constructed, under opaque ids) -> "
          f"{d / 'hand_label_sheet.json'} (read) and {d / 'hand_labels_template.json'} (fill in).")
    print("The sheet is blind: no judge verdict, no real/constructed marker. Do not open judge_verdicts.json or label_key.json before labelling.")
    return 0


def _cmd_agreement(args) -> int:
    d = _run_dir(args)
    vpath = Path(args.verdicts) if args.verdicts else d / "judge_verdicts.json"
    if not vpath.exists():
        raise SystemExit("error: no judge_verdicts.json (run `eval judge` first, or pass --verdicts)")
    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    result = judge.agreement(json.loads(vpath.read_text(encoding="utf-8")), labels)
    if args.key:
        result["human_vs_construction"] = judge.human_vs_construction(labels, json.loads(Path(args.key).read_text(encoding="utf-8")))
    (d / "agreement.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    if not result["measured"]:
        print(f"NOT MEASURED: 0 human-labelled outputs matched ({result['unlabelled']} rows unlabelled). Not 0%, not 100%: there is no denominator.")
    else:
        print(f"judge vs human: {result['agree']}/{result['n']} = {100 * result['agreement']:.1f}%  (threshold {result['threshold']:.0%}: "
              f"{'MET' if result['meets_threshold'] else 'NOT MET'})")
        if result["sample_warning"]:
            print("warning:", result["sample_warning"])
        for x in result["disagreements"]:
            print(f"  disagreement {x['id']}: human={x['human']} judge={x['judge']}")
        if "human_vs_construction" in result:
            print("context (your labels vs my construction of the bad answers):", json.dumps(result["human_vs_construction"]))
    return 0


def _cmd_golden(args) -> int:
    from . import golden

    base = runner.new_run_dir(DEFAULT_RUNS)
    run_dir = Path(args.out) if args.out else base.with_name("golden_" + base.name)
    env = Env(run_dir / "traces", live=True, pace_s=args.pace)
    if not env.live:
        raise SystemExit("error: the golden run needs the live model (set GROQ_API_KEY)")
    rows = [r for r in golden.load_golden() if not args.only or r["id"] in args.only]
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        results = golden.run(env, rows, run_dir / "golden_results.json", between_s=args.between)
    finally:
        env.close()
    print(f"{len(results)} questions -> {run_dir / 'golden_results.json'}")
    return 0


def _cmd_load(args) -> int:
    from . import golden, loadtest
    from .harness import Scripted, first_chunk_id

    out = Path(args.out) if args.out else DEFAULT_RUNS / "load"
    env = Env(out / "traces", live=False)
    answerable = [(r["question"], r["version"] or None) for r in golden.load_golden() if r["answerable"]]
    questions = [answerable[i % len(answerable)] for i in range(args.requests)]
    try:
        engine = env.engine(llm=Scripted(lambda m: f"Answer [{first_chunk_id(m)}]."), tools=env.mcp())
        engine.ask(*questions[0][:1], explicit_version=questions[0][1])        # warm-up: model load, first MCP call (not counted)
        rows = loadtest.run(engine, questions, [int(x) for x in args.levels.split(",")])
    finally:
        env.close()
    out.mkdir(parents=True, exist_ok=True)
    (out / "load_results.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return 1 if any(r["failures"] for r in rows) else 0


def _cmd_trajectory(args) -> int:
    """Re-score the trajectory questions from a saved golden run. No model call: the paths were scored against the specs when the run was made."""
    from . import trajectory

    path = Path(args.results)
    rows = json.loads(path.read_text(encoding="utf-8"))
    scored = [r for r in rows if r.get("score")]
    if not scored:
        raise SystemExit(f"error: no row in {path} has a trajectory score (were the questions {sorted(trajectory.SPECS)} run?)")
    summary = trajectory.summarize(scored)
    missing = sorted(set(trajectory.SPECS) - {r["id"] for r in scored})
    print(f"{'question':10} {'expected tools':44} {'actual tools':50} path outcome gap")
    for r in scored:
        sp, sc = trajectory.SPECS[r["id"]], r["score"]
        want = ", ".join(f"{t}x{n}" for t, n in sp.required.items()) + ("  [refusal expected]" if sp.negative else "")
        got = ", ".join(sc["tool_sequence"]) or "-"
        print(f"{r['id']:10} {want:44} {got:50} {'ok ' if sc['path_ok'] else 'NO '}  {sc['outcome']:9} {sc['gap']}")
    print("\n" + json.dumps(summary, indent=1))
    if missing:
        print(f"note: {len(missing)} spec'd question(s) are not in this file: {missing}")
    out = Path(args.out) if args.out else path.with_name("trajectory_summary.json")
    out.write_text(json.dumps({"summary": summary, "missing": missing}, indent=1), encoding="utf-8")
    print(f"written to {out}")
    return 0


def _cmd_history(args) -> int:
    """One line per saved run under the runs directory, oldest first, so a change between runs is visible."""
    base = Path(args.dir) if args.dir else DEFAULT_RUNS
    if not base.is_dir():
        print(f"no runs yet ({base} does not exist)")
        return 0
    lines = []
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        try:
            if (d / "summary.json").exists() and (d / "results.json").exists():
                s, meta = (json.loads((d / f).read_text(encoding="utf-8")) for f in ("summary.json", "results.json"))
                m = meta.get("meta", {})
                lines.append(f"{d.name:34} suite   {m.get('started', '')[:19]:19} {'live' if m.get('live') else 'offline':7} cases={s['cases']} executed={s['executed']} "
                             f"{s['status']} pass_rate={s['pass_rate_executed']}")
            elif (d / "golden_results.json").exists():
                g = json.loads((d / "golden_results.json").read_text(encoding="utf-8"))
                oc = {}
                for r in g:
                    oc[r["outcome"]] = oc.get(r["outcome"], 0) + 1
                lines.append(f"{d.name:34} golden  questions={len(g)} outcomes={oc}")
            elif (d / "load_results.json").exists():
                g = json.loads((d / "load_results.json").read_text(encoding="utf-8"))
                lines.append(f"{d.name:34} load    levels={[r['concurrency'] for r in g]} failures={sum(r['failures'] for r in g)}")
        except (OSError, ValueError, KeyError) as exc:
            lines.append(f"{d.name:34} unreadable ({type(exc).__name__})")
    print("\n".join(lines) if lines else f"no runs under {base}")
    return 0


def _cmd_regress(args) -> int:
    env = Env(Path(args.out) / "traces" if args.out else DEFAULT_RUNS / "regress_traces", live=False)
    try:
        rows = regress.red_green(env)
    finally:
        env.close()
    for r in rows:
        print(f"{r['id']}  RED={r['red']}  GREEN={r['green']}  {'VERIFIED' if r['verified'] else 'NOT VERIFIED'}  | {r['defect']}  | {r['origin']}")
        if r["failed_checks_when_red"]:
            print(f"      RED fails ({len(r['failed_checks_when_red'])}): {r['failed_checks_when_red']}")
    ok = all(r["verified"] for r in rows)
    print(f"\n{sum(r['verified'] for r in rows)}/{len(rows)} regression cases verified RED with the defect re-created and GREEN on the current code")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m sdk_engine eval", description="integrated evaluation suite")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("list").set_defaults(func=_cmd_list)
    r = sub.add_parser("run")
    r.add_argument("--live", action="store_true", help="also run the live-model cases (needs the API key; paced to the 8,000-tokens/minute limit)")
    r.add_argument("--only", nargs="*", help="run only these case ids")
    r.add_argument("--out", help="run directory (default: developer-rag/data/eval_runs/run_<utc>)")
    r.add_argument("--pace", type=float, default=17.0, help="minimum seconds between live model calls")
    r.set_defaults(func=_cmd_run)
    j = sub.add_parser("judge")
    j.add_argument("--run", required=True)
    j.add_argument("--pace", type=float, default=15.0)
    j.set_defaults(func=_cmd_judge)
    ls = sub.add_parser("label-sheet")
    ls.add_argument("--run", required=True)
    ls.set_defaults(func=_cmd_label_sheet)
    a = sub.add_parser("agreement")
    a.add_argument("--run", required=True)
    a.add_argument("--labels", required=True, help="the filled-in hand_labels_template.json")
    a.add_argument("--verdicts", help="judge_verdicts.json if it is not in the run directory")
    a.add_argument("--key", help="label_key.json (optional): also report how your labels compare with my construction of the bad answers")
    a.set_defaults(func=_cmd_agreement)
    g = sub.add_parser("regress")
    g.add_argument("--out")
    g.set_defaults(func=_cmd_regress)
    ld = sub.add_parser("load", help="bounded local load test of the engine's own path (instant scripted model; nothing is sent to a provider)")
    ld.add_argument("--levels", default="1,2,4,8", help="comma-separated concurrency levels")
    ld.add_argument("--requests", type=int, default=40, help="requests per level")
    ld.add_argument("--out")
    ld.set_defaults(func=_cmd_load)
    gd = sub.add_parser("golden", help="run the 45 golden questions live (trajectory + taxonomy sample)")
    gd.add_argument("--only", nargs="*")
    gd.add_argument("--out")
    gd.add_argument("--pace", type=float, default=17.0)
    gd.add_argument("--between", type=float, help="LATENCY run: do not pace the model inside the call; start a new question every N seconds instead "
                                                  "(use >= 20 to stay under an 8,000-token/minute key)")
    gd.set_defaults(func=_cmd_golden)
    tj = sub.add_parser("trajectory", help="re-score the 10 trajectory questions from a saved golden run (no model call)")
    tj.add_argument("--results", required=True, help="a golden_results.json")
    tj.add_argument("--out", help="where to write the summary (default: next to the results file)")
    tj.set_defaults(func=_cmd_trajectory)
    hs = sub.add_parser("history", help="one line per saved run (suite, golden, load), oldest first")
    hs.add_argument("--dir", help=f"runs directory (default: {DEFAULT_RUNS})")
    hs.set_defaults(func=_cmd_history)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
