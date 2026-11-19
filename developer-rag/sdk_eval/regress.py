"""Permanent RED/GREEN regression for REAL observed failures.

Each `reg*` case in cases.py came from a defect actually observed while building this system (see its `origin`). The original RED/GREEN
runs were recorded when each was found; this module makes them REPEATABLE: it re-creates the defect in-process (RED: the case must FAIL),
then runs the case on the current code (GREEN: it must PASS). A regression case that stays green with its defect re-created is not
protecting anything, and this command says so. Re-creating a defect is a simulation of the old behaviour, labelled as such.
"""

from __future__ import annotations

import re
from contextlib import ExitStack, contextmanager
from typing import Any, Dict, List, Optional, Sequence
from unittest import mock

from . import runner
from .harness import Env


@contextmanager
def _semver_counts_as_a_version_claim():
    """Phase 6 live defect: any known version label, of ANY product, was a claim ("v2.0.0" -> the Swagger label 2.x)."""
    from sdk_engine.versioning import VersionCatalog

    with mock.patch.object(VersionCatalog, "product_of", lambda self, version: None):
        yield


@contextmanager
def _bearer_echo_is_an_invented_secret():
    """Phase 7 live defect: the secret check compared the WHOLE match ('Bearer ghp_...') with the question, so the user's own token failed."""
    from sdk_engine import answering
    from sdk_engine.redaction import find_secrets

    orig = answering.output_safety_failures

    def old(answer, *, passages, question, system_prompt, **kw):
        out = orig(answer, passages=passages, question=question, system_prompt=system_prompt, **kw)
        source = "\n".join(p.text for p in passages) + "\n" + question
        if not any("secret-shaped" in f for f in out) and [s for s in find_secrets(answer) if s not in source]:
            out.append("security: the answer contains a secret-shaped value that is not in the sources")
        return out

    with mock.patch.object(answering, "output_safety_failures", old):
        yield


@contextmanager
def _shell_split_by_physical_line():
    """Phase 6 live defect: a shell block was split by physical line, so a multi-line -d '{...}' body became several 'commands'."""
    from sdk_engine import codecheck

    with mock.patch.object(codecheck, "_logical_lines", lambda text: re.sub(r"\\[ \t]*\r?\n", " ", text).splitlines()):
        yield


@contextmanager
def _degraded_prints_unchecked_text():
    """Phase 5 live defect: with no answer model, retrieved text was printed with no output checks."""
    from sdk_engine import answering

    with mock.patch.object(answering, "safe_excerpts", lambda passages, *, limit=2, **kw: (list(passages)[:limit], [])):
        yield


@contextmanager
def _dashes_not_normalised():
    """Phase 8 live defect: only an ASCII hyphen made a date, so a pasted date was 'unsupported' and a model's date was invisible to the claim check.
    Phase 9 added the output side: the model's look-alike hyphens (inside `okhttp-gson`, dates) were delivered to the user unchanged."""
    from sdk_engine import answering, versioning

    with mock.patch.object(versioning, "normalize_dashes", lambda text: text), mock.patch.object(answering, "ascii_hyphens", lambda text: text):
        yield


DEFECTS = {
    "reg05": ("a date with a non-breaking hyphen not recognised as a version", _dashes_not_normalised),
    "reg01": ("semver value read as a claim about another product's version", _semver_counts_as_a_version_claim),
    "reg02": ("the user's own token echoed in a Bearer header treated as an invented secret", _bearer_echo_is_an_invented_secret),
    "reg03": ("multi-line quoted curl body split into shell commands", _shell_split_by_physical_line),
    "reg04": ("degraded mode printing a poisoned excerpt unchecked", _degraded_prints_unchecked_text),
}


def red_green(env: Env, ids: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    out = []
    for spec in runner.select(ids=ids, regression=True):
        defect = DEFECTS.get(spec.id)
        green = runner.run_case(env, spec)
        row = {"id": spec.id, "title": spec.title, "origin": spec.origin, "defect": defect[0] if defect else None,
               "green": green["status"], "red": None, "verified": False, "failed_checks_when_red": []}
        if defect:
            with defect[1]():
                red = runner.run_case(env, spec)
            row["red"] = red["status"]
            row["failed_checks_when_red"] = [c["name"] for c in red["checks"] if not c["passed"]]
            row["verified"] = red["status"] == "fail" and green["status"] == "pass"
        out.append(row)
    return out
