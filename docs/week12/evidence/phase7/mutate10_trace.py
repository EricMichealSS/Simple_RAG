import os
import re
import sys


def pytest_configure(config):
    m = os.environ.get("MUTATION", "")
    sys.path.insert(0, r"C:/Projects/AI Learning/Simple_RAG/developer-rag")
    import sdk_engine.graph as G
    import sdk_engine.tracequery as T

    if m == "no_summary":                   # the engine stops writing the request summary
        G.request_summary = lambda state, spans: {}
    if m == "summary_drops_tools":          # the summary forgets which tools were called
        orig = G.request_summary
        G.request_summary = lambda state, spans: {**orig(state, spans), "tools_called": []}
    if m == "no_provider_or_prompt":        # the generate span stops recording provider/prompt version
        orig_s = G.answering.PROMPT_VERSION
        G.answering.PROMPT_VERSION = None
    if m == "cost_zero_when_unpriced":      # an unpriced model is shown as $0
        orig_sc = T.stage_costs

        def zero(spans):
            out = orig_sc(spans)
            for s in out["stages"]:
                if s["stage"] == "generate" and s["api_cost_usd"] is None:
                    s["api_cost_usd"] = 0.0
            out["api_cost_usd"] = out["api_cost_usd"] or 0.0
            return out
        T.stage_costs = zero
    if m == "no_redaction_on_show":         # `show` prints whatever is in the file
        T.redact_obj = lambda x: x
    if m == "unpriced_counts_as_priced":    # outliers/percentiles include requests with no calculated cost
        T.stats.__globals__["percentile"] = T.percentile
        orig_co = T.cost_outliers
        T.cost_outliers = lambda reqs, factor=3.0: orig_co([r for r in reqs], factor)
        T.MIN_FOR_OUTLIERS = 1
    if m == "server_not_joined":            # `show` reads only the client log
        T.SERVER_FILE = "nonexistent_server_spans.jsonl"
    if m == "since_ignored":                # time range filters do nothing
        orig_m = T.matches

        def loose(r, q):
            q2 = T.Query(**{**q.__dict__, "since": None, "until": None})
            return orig_m(r, q2)
        T.matches = loose
    print(f"\n*** MUTATION APPLIED: {m or 'none'} ***")
