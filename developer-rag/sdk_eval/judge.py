"""The LLM judge, its blind hand-label sheet, and the agreement calculation.

Rules this module enforces (the brief: "do not fabricate or silently relabel the ground truth to achieve the threshold"):
* The judge NEVER decides whether a case passes. Deterministic assertions do. The judge is a secondary signal until its agreement with
  HUMAN labels on this system's outputs reaches 85% on a denominator large enough to mean something.
* The hand-label sheet shown to the human is BLIND: it never contains the judge's verdict.
* Agreement is computed only from labels a human wrote. With no labels the result is `measured: false`, denominator 0 - not 0%, not 100%.
* A judge reply that cannot be parsed counts as a disagreement, not as a skipped row.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from sdk_engine.config import EngineConfig, get_api_key
from sdk_engine.pricing import cost_usd

JUDGE_MODEL = "openai/gpt-oss-20b"
PROMPT_FILE = Path(__file__).with_name("judge_prompt.txt")
MIN_N = 20
THRESHOLD = 0.85

LABEL_RULE = ("PASS = the answer is correct for the caller's version, supported by the context shown, and would let a developer move on "
              "(or it is a correct 'I don't know' because the context really contains no answer). FAIL = wrong, invented, an unnecessary "
              "'I don't know' when the context has the answer, or a one-sided answer to a comparison. Judge the answer text only: code "
              "parsing, symbol existence and the version label are checked by deterministic assertions, not by you.")


def strip_footer(answer: str) -> str:
    return answer.split("\n\nCode check")[0]


def parse_verdict(text: str) -> str:
    m = re.search(r"VERDICT:\s*(PASS|FAIL)", text or "", re.IGNORECASE)
    return m.group(1).upper() if m else "UNPARSED"


def build_prompt(template: str, o: Dict[str, Any]) -> str:
    context = "\n\n".join(f"[{c['chunk_id']}]\n{c['text']}" for c in o["context"]) or "(nothing was retrieved)"
    version = ", ".join(o.get("comparison_versions") or []) or str(o.get("caller_version"))
    return template.format(question=o["question"], version=version, context=context, answer=strip_footer(o["answer"]))


def judge_outputs(outputs: Sequence[Dict[str, Any]], *, cfg: Optional[EngineConfig] = None, pace_s: float = 15.0, progress=print) -> List[Dict[str, Any]]:
    """Grade each output with the judge model. Calls are paced to the provider's token limit; one retry after a rate-limit error."""
    from openai import OpenAI

    cfg = cfg or EngineConfig.from_env()
    key = get_api_key()
    if not key:
        raise RuntimeError("the judge needs the API key (GROQ_API_KEY, or the legacy GROK_API_KEY)")
    client = OpenAI(api_key=key, base_url=cfg.llm_base_url, timeout=60.0, max_retries=0)
    template = PROMPT_FILE.read_text(encoding="utf-8")
    verdicts: List[Dict[str, Any]] = []
    for i, o in enumerate(outputs, 1):
        reply = None
        for attempt in range(2):
            try:
                reply = client.chat.completions.create(
                    model=JUDGE_MODEL, temperature=0.0, max_tokens=600, reasoning_effort="low",
                    messages=[{"role": "system", "content": "You are a strict, careful grading assistant."}, {"role": "user", "content": build_prompt(template, o)}])
                break
            except Exception as exc:                 # class name only: provider errors can echo request details
                if attempt == 0 and "RateLimit" in type(exc).__name__:
                    time.sleep(45)
                    continue
                verdicts.append({"id": o["id"], "verdict": "ERROR", "raw": type(exc).__name__, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": None})
                break
        if reply is not None:
            text = (reply.choices[0].message.content or "").strip()
            u = reply.usage
            verdicts.append({"id": o["id"], "verdict": parse_verdict(text), "raw": text[:400], "prompt_tokens": u.prompt_tokens,
                             "completion_tokens": u.completion_tokens, "cost_usd": cost_usd(JUDGE_MODEL, u.prompt_tokens, u.completion_tokens)})
        progress(f"[{i}/{len(outputs)}] judged {o['id']}: {verdicts[-1]['verdict']}")
        time.sleep(pace_s)
    return verdicts


def _banner(answer: str) -> str:
    m = re.match(r"\[[^\]]+\]\s", answer)
    return m.group(0) if m else ""


def controlled_negatives(outputs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deliberately WRONG answers derived from the real ones, so the judge check has bad answers in it (the live model's own answers all
    passed the deterministic assertions, and agreement on an all-PASS sample says nothing about catching a wrong answer). Each is built by
    a stated rule from a real output and uses that output's real context. They are CONTROLLED FIXTURES made by me, not observations; the
    human's label is still the ground truth for every item, including these."""
    by = {o["id"]: o for o in outputs}
    made: List[Dict[str, Any]] = []

    def add(source: str, kind: str, answer: str) -> None:
        o = by.get(source)
        if o is not None and answer.strip() and answer != o["answer"]:
            made.append({**o, "id": f"neg:{source}:{kind}", "answer": answer, "_kind": kind, "_source": source})

    for source, (old, new) in {"g01#1": ("201", "200"), "g02#1": ("public_repo", "read:user"), "g03#1": ("assignees", "assignee_ids"),
                               "g04#1": ("--generate-markdown", "--generate-docs"), "g05#1": ("internal", "confidential")}.items():
        if source in by:
            add(source, "wrong_fact", by[source]["answer"].replace(old, new))
    for source in ("g03#1", "g05#1", "g06#1"):                        # the context DOES contain the answer: an "I don't know" is a false refusal
        if source in by:
            add(source, "false_refusal", _banner(by[source]["answer"]) + "I don't know based on the provided documents.")
    for source, other in (("g02#1", "g01#1"), ("g04#1", "g05#1")):   # a fluent answer to a different question
        if source in by and other in by:
            add(source, "answers_a_different_question", by[other]["answer"])
    for source in ("g07#1", "g08#1"):                                  # a comparison answered from one side only
        if source in by:
            body = by[source]["answer"].split("\n\n")
            add(source, "one_sided_comparison", body[0] if body[0] != by[source]["answer"] else "")
    if "g09#1" in by:                                                  # the question is unanswerable from the corpus: a confident invented figure
        add("g09#1", "fabricated_answer", _banner(by["g09#1"]["answer"]) + "Unauthenticated GraphQL queries are limited to 60 requests per hour "
            "[github_combined_reference:p1:spec:top:0].")
    return made


def build_label_set(outputs: Sequence[Dict[str, Any]], seed: int = 7) -> tuple:
    """(items, key). Real outputs and controlled negatives, shuffled and given OPAQUE ids (item01...), so nothing in the sheet or the
    template says which is which. `key` maps each opaque id back (kept apart from the sheet: do not read it before labelling)."""
    import random

    pool = [dict(o, _kind="real", _source=o["id"]) for o in outputs] + controlled_negatives(outputs)
    random.Random(seed).shuffle(pool)
    items, key = [], {}
    for n, o in enumerate(pool, 1):
        oid = f"item{n:02d}"
        key[oid] = {"kind": o["_kind"], "source": o["_source"], "expected_by_construction": "FAIL" if o["_kind"] != "real" else None}
        items.append({k: v for k, v in o.items() if not k.startswith("_")} | {"id": oid})
    return items, key


def human_vs_construction(labels: Sequence[Dict[str, Any]], key: Dict[str, Any]) -> Dict[str, Any]:
    """Context for reading an agreement number: how often the HUMAN's label differs from my construction (a constructed-bad answer the human
    passes, or a real answer the human fails). The human label always stands; this only shows how far my construction was from their judgement."""
    human = {l["id"]: str(l.get("label")).upper() for l in labels if str(l.get("label")).upper() in ("PASS", "FAIL")}
    neg = [i for i in human if key.get(i, {}).get("kind", "real") != "real"]
    real = [i for i in human if key.get(i, {}).get("kind") == "real"]
    return {"labelled_constructed": len(neg), "human_passed_a_constructed_bad_answer": sum(human[i] == "PASS" for i in neg),
            "labelled_real": len(real), "human_failed_a_real_answer": sum(human[i] == "FAIL" for i in real)}


def judge_on_controlled_items(verdicts: Sequence[Dict[str, Any]], key: Dict[str, Any]) -> Dict[str, Any]:
    """INFORMATIONAL, and NOT human labels: how the judge treated the answers I deliberately corrupted, and the real ones. This measures the
    judge against MY construction (a sanity check on whether it can say FAIL at all), not against ground truth."""
    judged = {v["id"]: v["verdict"] for v in verdicts}
    neg = [i for i, k in key.items() if k["kind"] != "real" and i in judged]
    real = [i for i, k in key.items() if k["kind"] == "real" and i in judged]
    by_kind: Dict[str, Dict[str, int]] = {}
    for i in neg:
        row = by_kind.setdefault(key[i]["kind"], {"items": 0, "judge_said_fail": 0})
        row["items"] += 1
        row["judge_said_fail"] += judged[i] == "FAIL"
    return {"controlled_negatives": len(neg), "judge_said_fail": sum(judged[i] == "FAIL" for i in neg),
            "judge_caught_rate_vs_construction": round(sum(judged[i] == "FAIL" for i in neg) / len(neg), 3) if neg else None,
            "by_kind": by_kind, "real_outputs": len(real), "real_judged_pass": sum(judged[i] == "PASS" for i in real),
            "note": "measured against my construction of known-bad answers, NOT against human labels; not the 85% check"}


def label_sheet(outputs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """What the human reads. BLIND: no judge verdict, no deterministic result, no case status."""
    return {"how_to_label": LABEL_RULE,
            "how_to_return": "Copy hand_labels_template.json, set each \"label\" to \"PASS\" or \"FAIL\" (and optionally a one-line reason). Leave a row's label null "
                             "to skip it. Do not look at judge_verdicts.json until you have finished.",
            "items": [{"id": o["id"], "question": o["question"], "caller_version": o.get("caller_version"),
                       "comparison_versions": o.get("comparison_versions") or [], "answer_shown_to_the_user": o["answer"],
                       "context_the_assistant_was_shown": o["context"]} for o in outputs]}


def label_template(outputs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"id": o["id"], "label": None, "reason": ""} for o in outputs]


def agreement(verdicts: Sequence[Dict[str, Any]], labels: Sequence[Dict[str, Any]], *, threshold: float = THRESHOLD, min_n: int = MIN_N) -> Dict[str, Any]:
    """Judge versus HUMAN labels. Rows without a human PASS/FAIL are not counted; a judge reply that is not PASS/FAIL counts as a disagreement."""
    judged = {v["id"]: v["verdict"] for v in verdicts}
    human = {l["id"]: str(l.get("label")).upper() for l in labels if str(l.get("label")).upper() in ("PASS", "FAIL")}
    compared = [(i, h, judged[i]) for i, h in human.items() if i in judged]
    agree = sum(1 for _, h, j in compared if h == j)
    n = len(compared)
    return {
        "measured": n > 0, "n": n, "agree": agree, "agreement": round(agree / n, 4) if n else None,
        "threshold": threshold, "meets_threshold": (agree / n >= threshold) if n else None,
        "sample_warning": None if n >= min_n else f"n={n} is below {min_n}: too small to claim the judge meets or misses {threshold:.0%}",
        "labelled_but_not_judged": sorted(set(human) - set(judged)), "unlabelled": len(labels) - len(human),
        "judge_unparsed_or_error": sum(1 for _, _, j in compared if j not in ("PASS", "FAIL")),
        "disagreements": [{"id": i, "human": h, "judge": j} for i, h, j in compared if h != j],
    }


def judge_vs_deterministic(results: Sequence[Dict[str, Any]], verdicts: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """INFORMATIONAL ONLY, and NOT hand labels: how often the judge agrees with the suite's own deterministic verdict for the case an output
    came from. Deterministic assertions can be wrong too; this is a consistency signal, not the 85% check."""
    case_status = {r["id"]: r["status"] for r in results if r["status"] in ("pass", "fail")}
    judged = {v["id"]: v["verdict"] for v in verdicts}
    pairs = [(i, "PASS" if case_status[i.split("#")[0]] == "pass" else "FAIL", v) for i, v in judged.items() if i.split("#")[0] in case_status]
    agree = sum(1 for _, d, v in pairs if d == v)
    return {"n": len(pairs), "agree": agree, "agreement": round(agree / len(pairs), 4) if pairs else None,
            "note": "judge vs the suite's deterministic case verdict; NOT human labels; not the 85% check",
            "disagreements": [{"id": i, "deterministic": d, "judge": v} for i, d, v in pairs if d != v]}
