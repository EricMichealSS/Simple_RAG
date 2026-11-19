"""Does the suite fail when the engine is broken? Nine engine-level defects are re-created in-process (one at a time) and the OFFLINE tier is
run under each; a defect is 'detected' if at least one case fails (or errors). This measures the SUITE, not the engine. The patches are a
simulation of each defect, not the historical code. Usage: python suite_sensitivity.py <scratch_dir> [--json out.json]"""
import json
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

sys.path.insert(0, r"C:/Projects/AI Learning/Simple_RAG/developer-rag")
from sdk_engine import answering, codecheck, graph, retrieval, toolbox, versioning
from sdk_engine.retrieval import ChromaRetriever
from sdk_engine.schemas import Product, VersionResolution, VersionSource, VersionStatus
from sdk_engine.uploads import UploadStore
from sdk_eval import runner
from sdk_eval.harness import Env, LeakyCollection


class _NoRedaction:
    counts = {}
    text = ""


def mutations(env):
    leaky = lambda self, collection=None: ChromaRetriever(LeakyCollection(collection or self.collection), self.embed, max_top_k=self.cfg.max_top_k, hybrid=False)   # noqa: E731
    orig_search = UploadStore.search

    def anyone_sees_alices(self, owner_id, query, top_k=5):
        return orig_search(self, "alice", query, top_k)

    def guess_a_version(question, *, catalog, explicit=None, config_defaults=None):
        return VersionResolution(status=VersionStatus.RESOLVED, product=Product.GITHUB_REST, version="2022-11-28", source=VersionSource.CONFIG, confidence=0.3)

    def tool_blows_up(*a, **k):
        raise RuntimeError("tool layer crashed")

    return {
        "broken_filter_and_no_post_check": [mock.patch.object(Env, "retriever", leaky), mock.patch.object(retrieval, "enforce_scope", lambda *a, **k: None),
                                            mock.patch.object(toolbox, "enforce_scope", lambda *a, **k: None)],
        "output_safety_checks_off": [mock.patch.object(answering, "output_safety_failures", lambda *a, **k: [])],
        "code_validation_off": [mock.patch.object(codecheck, "validate_code", lambda *a, **k: None)],
        "uploads_not_isolated_by_owner": [mock.patch.object(UploadStore, "search", anyone_sees_alices)],
        "unknown_version_is_guessed_not_asked": [mock.patch.object(graph, "resolve_version", guess_a_version)],
        "tool_failure_not_handled": [mock.patch.object(toolbox, "search_via_tools", tool_blows_up)],
        "pii_redaction_off": [mock.patch.object(graph, "redact_with_counts", lambda q: _NoRedaction())],
        "changelog_lookup_returns_nothing": [mock.patch.object(toolbox, "changes_via_tools", lambda *a, **k: ([], []))],
        "citation_verification_off": [mock.patch.object(answering, "verify_answer", lambda answer, passages, allowed, catalog, truncated=False: (True, [], []))],
    }


def main():
    base = Path(sys.argv[1])
    env = Env(base / "traces", live=False)
    specs = runner.select(tier="offline")
    quiet = lambda *_: None                                                                       # noqa: E731
    out = {"cases": len(specs), "baseline_failures": [], "mutations": {}}
    try:
        base_res = runner.run_suite(env, specs, progress=quiet)
        out["baseline_failures"] = [r["id"] for r in base_res if r["status"] != "pass"]
        print(f"baseline: {len(specs)} offline cases, failing: {out['baseline_failures'] or 'none'}")
        for name in mutations(env):
            with ExitStack() as stack:
                for p in mutations(env)[name]:
                    stack.enter_context(p)
                res = runner.run_suite(env, specs, progress=quiet)
            caught = [r["id"] for r in res if r["status"] != "pass"]
            out["mutations"][name] = caught
            print(f"{name:38} {'DETECTED' if caught else 'MISSED  '} by {len(caught):2} case(s): {caught}")
    finally:
        env.close()
    detected = sum(bool(v) for v in out["mutations"].values())
    out["detected"], out["total"] = detected, len(out["mutations"])
    print(f"\n{detected}/{len(out['mutations'])} injected engine defects were detected by at least one offline case")
    if "--json" in sys.argv:
        Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(out, indent=1), encoding="utf-8")


main()
