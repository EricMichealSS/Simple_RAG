import os
import sys


def pytest_configure(config):
    m = os.environ.get("MUTATION", "")
    sys.path.insert(0, r"C:/Projects/AI Learning/Simple_RAG/developer-rag")
    import sdk_engine.codecheck as C
    import sdk_engine.tracequery as T

    if m == "footer_silent_on_unchecked":      # the original: not_checked identifiers are not mentioned in the footer
        orig = C.render_footer

        def quiet(report):
            kept = []
            for b in report.blocks:
                b2 = b.model_copy(update={"findings": [f for f in b.findings if f.tier != "not_checked"]})
                kept.append(b2)
            return orig(report.model_copy(update={"blocks": kept}))
        C.render_footer = quiet
    if m == "no_reason_codes":                 # unchecked findings stop saying why
        orig_c = C._classify

        def blank(sym, kind, ctx, endpoint):
            f = orig_c(sym, kind, ctx, endpoint)
            f.reason_code = None
            return f
        C._classify = blank
    if m == "fallback_filter_ignored":
        orig_m = T.matches
        T.matches = lambda r, q: orig_m(r, T.Query(**{**q.__dict__, "fallback": False}))
    if m == "as_of_ignored":                   # relative times always use the real clock
        orig_pw = T.parse_when
        T.parse_when = lambda text, *, now=None: orig_pw(text, now=None)
    if m == "timeline_counts_only_requests":
        orig_t = T.timeline

        def flat(requests, bucket="day"):
            rows = orig_t(requests, bucket)
            for r in rows:
                r["mcp_fallback_requests"] = 0
                r["code_unchecked_requests"] = 0
            return rows
        T.timeline = flat
    print(f"\n*** MUTATION APPLIED: {m or 'none'} ***")
