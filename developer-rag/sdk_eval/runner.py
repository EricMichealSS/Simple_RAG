"""Run the integrated suite and aggregate the metrics Phase 8 requires. Nothing here invents a number: every figure is computed from the
results of the run it is printed with, and a metric with no data says so."""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from sdk_engine.answering import MAX_PASSAGE_CHARS

from . import cases as C
from .harness import Env, Rec, Skip

NON_COVERAGE = [
    "Version-specific PAGINATION and removed-method scenarios are not in the indexed corpus (the brief's example); the version-specific "
    "changes that ARE in it (assignee -> assignees, make_latest, issue type, visibility, --generate-markdown, the asyncio library option) are covered.",
    "There is no community content in the corpus; `o01` is a controlled fixture (one planted page), not an observation of real community content.",
    "The engine has no answer or retrieval cache; `x02`/`x03` check invalidation of uploads and the absence of cross-request state, not a cache.",
    "The Week 6 suite's 26 cases are tied to Stack A's corpus and are preserved untouched; they were not run (and cannot be asked of this engine).",
    "Scripted-model cases simulate a model behaviour (an obedient model, a wrong-version sample); they test the engine's defences, not how often a real model misbehaves.",
    "Live cases are a small sample (see the case count), one run each, temperature 0 on a provider that is not perfectly deterministic.",
    "Hostile-MCP-server behaviour (poisoned tool descriptions/results) is covered by unit tests (tests/test_security_adversarial.py), not by this suite.",
]


def select(ids: Optional[Sequence[str]] = None, tier: Optional[str] = None, regression: Optional[bool] = None) -> List[C.Spec]:
    out = [s for s in C.CASES if (not ids or s.id in ids) and (tier is None or s.tier == tier) and (regression is None or s.regression == regression)]
    return out


def run_case(env: Env, spec: C.Spec) -> Dict[str, Any]:
    rec = Rec(env, spec)
    t0 = time.perf_counter()
    error: Optional[str] = None
    try:
        spec.fn(rec)
        status = "pass" if rec.checks and all(c.passed for c in rec.checks) else "fail"
    except Skip as skip:
        status, error = "skipped", str(skip)
    except Exception as exc:   # a case that crashes is an `error`, never a silent pass; the checks recorded before the crash are kept
        status, error = "error", f"{type(exc).__name__}: {str(exc)[:300]}"
    priced = [r.usage.cost_usd for r in rec.results if r.usage.cost_status == "calculated" and r.usage.cost_usd is not None]
    code = []
    for r in rec.results:
        if r.code_validation is not None and r.code_validation.blocks:
            rep = r.code_validation
            code.append({"live": not (r.usage.model or "").startswith("scripted"), "outcome": r.outcome, "hard": rep.hard, "soft": rep.soft,
                         "syntax": [b.syntax for b in rep.blocks], "languages": [b.language for b in rep.blocks],
                         "tiers": sorted({f.tier for b in rep.blocks for f in b.findings})})
    return {
        "id": spec.id, "title": spec.title, "category": spec.category, "covers": list(spec.covers), "modes": list(spec.modes), "origin": spec.origin,
        "tier": spec.tier, "regression": spec.regression, "status": status, "error": error,
        "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in rec.checks],
        "trace_ids": [r.trace_id for r in rec.results], "asks": len(rec.results),
        "live_llm_calls": sum(r.usage.llm_calls for r in rec.results if not (r.usage.model or "").startswith("scripted")),
        "scripted_llm_calls": sum(r.usage.llm_calls for r in rec.results if (r.usage.model or "").startswith("scripted")),
        "cost_usd": round(sum(priced), 8) if priced else None,
        "unpriced_calls": sum(r.usage.llm_calls for r in rec.results if r.usage.llm_calls and r.usage.cost_status != "calculated"),
        "duration_s": round(time.perf_counter() - t0, 2),
        "retrieved": rec.retrieved, "wrong_version_chunks": rec.wrong_version_chunks,
        "citations_total": rec.citations_total, "citations_bad": rec.citations_bad,
        "live_code_samples": rec.live_code_samples, "live_wrong_version_samples": rec.live_wrong_version_samples,
        "code": code, "notes": rec.notes,
        "_outputs": _outputs(env, rec, spec),
    }


def _outputs(env: Env, rec: Rec, spec: C.Spec) -> List[Dict[str, Any]]:
    """The LIVE model's answers (official corpus), with the exact context it was shown: what the judge grades and what a human labels."""
    out = []
    for n, res in enumerate(rec.results, 1):
        if spec.tier != "live" or res.corpus != "official" or res.outcome not in ("answered", "abstain") or (res.usage.model or "").startswith("scripted"):
            continue
        got = env.collection.get(ids=res.retrieved_chunk_ids, include=["documents"]) if res.retrieved_chunk_ids else {"ids": [], "documents": []}
        text = dict(zip(got["ids"], got["documents"]))
        out.append({"id": f"{spec.id}#{n}", "case": spec.id, "trace_id": res.trace_id, "question": rec.questions.get(res.trace_id, ""),
                    "caller_version": res.version.version if res.version else None,
                    "comparison_versions": list(res.version.comparison_versions) if res.version else [],
                    "outcome": res.outcome, "answer": res.answer,
                    "context": [{"chunk_id": c, "text": text.get(c, "")[:MAX_PASSAGE_CHARS]} for c in res.retrieved_chunk_ids]})
    return out


def run_suite(env: Env, specs: Sequence[C.Spec], progress=print) -> List[Dict[str, Any]]:
    results = []
    for i, spec in enumerate(specs, 1):
        r = run_case(env, spec)
        results.append(r)
        bad = [c["name"] for c in r["checks"] if not c["passed"]]
        progress(f"[{i:>2}/{len(specs)}] {spec.id:6} {spec.tier:7} {r['status'].upper():7} {r['duration_s']:>6.1f}s  {spec.title[:70]}"
                 + (f"\n          FAILED: {bad[:3]}" if bad else "") + (f"\n          {r['error']}" if r["error"] and r["status"] != "skipped" else ""))
    return results


def _rate(p: int, n: int) -> Optional[float]:
    return round(p / n, 3) if n else None


def summarize(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    executed = [r for r in results if r["status"] != "skipped"]
    counts = Counter(r["status"] for r in results)

    def group(keys_of) -> Dict[str, Any]:
        table: Dict[str, Dict[str, int]] = {}
        for r in results:
            for k in keys_of(r):
                row = table.setdefault(k, {"cases": 0, "pass": 0, "fail": 0, "error": 0, "skipped": 0})
                row["cases"] += 1
                row[r["status"]] += 1
        for row in table.values():
            ran = row["pass"] + row["fail"] + row["error"]
            row["pass_rate"] = _rate(row["pass"], ran)
            row["denominator"] = ran
        return dict(sorted(table.items()))

    retrieved = sum(r["retrieved"] for r in results)
    wrong = sum(r["wrong_version_chunks"] for r in results)
    live_samples = sum(r["live_code_samples"] for r in results)
    live_wrong = sum(r["live_wrong_version_samples"] for r in results)
    cit_total = sum(r["citations_total"] for r in results)
    cit_bad = sum(r["citations_bad"] for r in results)
    code = [c for r in results for c in r["code"]]
    priced = [r["cost_usd"] for r in results if r["cost_usd"] is not None]
    blocked = next((r["notes"].get("blocked") for r in results if r["id"] == "k01"), None)
    return {
        "cases": len(results), "executed": len(executed), "status": dict(counts),
        "pass_rate_executed": _rate(counts.get("pass", 0), len(executed)),
        "by_taxonomy_mode": group(lambda r: r["modes"]),
        "by_category": group(lambda r: [r["category"]] + list(r["covers"])),
        "by_tier": group(lambda r: [r["tier"]]),
        "retrieval_version_filter": {"chunks_returned": retrieved, "wrong_version_chunks": wrong, "wrong_version_rate": _rate(wrong, retrieved),
                                     "endpoint_recall": next((r["notes"].get("endpoint_recall") for r in results if r["id"] == "f01"), None)},
        "wrong_version_code": {"live_code_samples": live_samples, "live_wrong_version_delivered": live_wrong,
                               "live_rate": _rate(live_wrong, live_samples), "scripted_wrong_version_samples_blocked": blocked},
        "code_outcomes": {"samples_with_code": len(code), "live": sum(c["live"] for c in code), "scripted": sum(not c["live"] for c in code),
                          "syntax": dict(Counter(s for c in code for s in c["syntax"])), "languages": dict(Counter(l for c in code for l in c["languages"])),
                          "tiers": dict(Counter(t for c in code for t in c["tiers"])), "delivered_with_hard_finding": sum(c["hard"] > 0 and c["outcome"] == "answered" for c in code)},
        "citations_live": {"citations": cit_total, "incorrect": cit_bad, "correct_rate": _rate(cit_total - cit_bad, cit_total),
                           "definition": "retrieved in this request AND api_version within the allowed versions AND has source_name and location (deterministic; "
                                         "whether a citation SUPPORTS its claim is not checked here)"},
        "cost": {"priced_api_cost_usd": round(sum(priced), 6) if priced else None, "live_llm_calls": sum(r["live_llm_calls"] for r in results),
                 "scripted_llm_calls_unpriced": sum(r["scripted_llm_calls"] for r in results)},
        "non_coverage": NON_COVERAGE,
    }


def save(run_dir: Path, results: List[Dict[str, Any]], summary: Dict[str, Any], meta: Dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    outputs = [o for r in results for o in r.pop("_outputs", [])]
    (run_dir / "results.json").write_text(json.dumps({"meta": meta, "results": results}, indent=1), encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    (run_dir / "outputs.json").write_text(json.dumps(outputs, indent=1), encoding="utf-8")
    (run_dir / "report.md").write_text(render_report(results, summary, meta), encoding="utf-8")


def new_run_dir(base: Path) -> Path:
    return base / datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")


def render_report(results: Sequence[Dict[str, Any]], s: Dict[str, Any], meta: Dict[str, Any]) -> str:
    pct = lambda x: "n/a" if x is None else f"{100 * x:.1f}%"                                  # noqa: E731
    lines = [f"# Integrated evaluation run — {meta.get('started', '')}", "",
             f"cases: {s['cases']} · executed: {s['executed']} · status: {s['status']} · pass rate over executed cases: {pct(s['pass_rate_executed'])}", "",
             "## Cases", "", "| id | tier | status | category | modes | origin | title |", "|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['id']} | {r['tier']} | {r['status']} | {r['category']} | {', '.join(r['modes'])} | {r['origin']} | {r['title']} |")
    lines += ["", "## Failed checks", ""]
    failed = [(r["id"], c) for r in results for c in r["checks"] if not c["passed"]]
    lines += [f"* **{i}** — {c['name']} — {c['detail']}" for i, c in failed] or ["none"]
    lines += ["", "## Pass rate by taxonomy mode (denominator = cases executed that carry the mode)", "", "| mode | cases | executed | pass | rate |", "|---|---|---|---|---|"]
    for k, v in s["by_taxonomy_mode"].items():
        lines.append(f"| {k} | {v['cases']} | {v['denominator']} | {v['pass']} | {pct(v['pass_rate'])} |")
    rv, wv, co, ci = s["retrieval_version_filter"], s["wrong_version_code"], s["code_outcomes"], s["citations_live"]
    lines += ["", "## Metrics", "",
              f"* retrieval / version filter: {rv['wrong_version_chunks']} wrong-version chunks of {rv['chunks_returned']} returned ({pct(rv['wrong_version_rate'])}); endpoint recall {rv['endpoint_recall']}",
              f"* wrong-version code samples DELIVERED (live model): {wv['live_wrong_version_delivered']} of {wv['live_code_samples']} ({pct(wv['live_rate'])}); scripted wrong-version samples blocked: {wv['scripted_wrong_version_samples_blocked']}",
              f"* code outcomes: {co['samples_with_code']} answers with code ({co['live']} live, {co['scripted']} scripted); syntax {co['syntax']}; tiers {co['tiers']}; delivered with a blocking finding: {co['delivered_with_hard_finding']}",
              f"* citation correctness (live answers): {ci['citations'] - ci['incorrect']} of {ci['citations']} ({pct(ci['correct_rate'])}); {ci['definition']}",
              f"* priced API cost of this run: {s['cost']['priced_api_cost_usd']} USD over {s['cost']['live_llm_calls']} live model calls; {s['cost']['scripted_llm_calls_unpriced']} scripted calls (unpriced by design)",
              "", "## Not covered", ""] + [f"* {x}" for x in s["non_coverage"]]
    return "\n".join(lines) + "\n"
