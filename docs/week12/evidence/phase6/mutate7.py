import os
import re
import sys


def pytest_configure(config):
    m = os.environ.get("MUTATION", "")
    sys.path.insert(0, r"C:/Projects/AI Learning/Simple_RAG/developer-rag")
    import sdk_engine.codecheck as C
    import sdk_engine.symbols as S
    import sdk_engine.toolbox as T

    if m == "no_sentence_cut":              # the index's absence window crosses sentence boundaries again
        def old(before, after):
            return any(x in before for x in S._ABSENCE_BEFORE) or before.endswith("no") or any(x in after for x in S._ABSENCE_AFTER)
        S._says_absent = old
    if m == "cited_tier_lenient":           # 'assignees' counts as shown by 'assignee string' (squashed match)
        orig = C.appears_in
        C.appears_in = lambda t, s, squash=False: orig(t, s, squash=True)
    if m == "validation_off":               # the graph never validates code
        C.validate_code = lambda *a, **k: None
    if m == "hard_never_blocks":            # hard findings are only reported
        C.failures_for = lambda report, retries_left: []
    if m == "soft_always_blocks":           # a soft finding can abstain an otherwise good answer
        orig_f = C.failures_for
        C.failures_for = lambda report, retries_left: orig_f(report, retries_left=True)
    if m == "endpoint_scope_off":           # parameter symbols are never scoped to the endpoint
        C._PARAM_KINDS = set()
    if m == "tool_answer_trusted":          # accept a check_symbol finding without checking it answers for OUR version
        def lenient(gateway, trace, version, symbol, endpoint):
            args = {"symbol": symbol}
            if endpoint:
                args["endpoint"] = endpoint
            data = T._invoke(gateway, trace, "check_symbol", args, {"version": version}, rationale="x")
            return T.SymbolFinding(**{k: v for k, v in data.items() if k in T.SymbolFinding.model_fields})
        T.check_symbol_via_tools = lenient
    if m == "no_sanitising":                # evidence from another process reaches user-visible text untouched
        C._clean = lambda v: str(v or "")
    if m == "python_syntax_warn_only":      # a python sample that does not parse is only a warning
        orig_vb = C._validate_block

        def soft(n, rb, ctx):
            rep = orig_vb(n, rb, ctx)
            for f in rep.findings:
                if f.kind == "syntax":
                    f.severity = "warn"
            return rep
        C._validate_block = soft
    print(f"\n*** MUTATION APPLIED: {m or 'none'} ***")
