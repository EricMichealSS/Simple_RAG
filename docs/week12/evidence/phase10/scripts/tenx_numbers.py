"""The arithmetic behind docs/week12/tenx.md. Every INPUT is either measured (named with its source file) or an ASSUMPTION (labelled); nothing here is a measurement of production traffic.
Run: python tenx_numbers.py"""
import json

# ---- measured inputs (source in the comment)
TOKENS_PER_QUERY = 1836            # mean of 45 golden questions, evidence/phase9/golden_analysis.json
COST_PER_QUERY = 0.000388          # same run, USD calculated from tokens and the price table
TPD_FREE = 200_000                 # tokens/day for gpt-oss-120b on this key, provider's own 429 message, evidence/phase10/provider_limits/probe_4_daily_quota_429.json
TPM_FREE = 8_000                   # x-ratelimit-limit-tokens header, probe_1_headers.json
RPD_FREE = 1_000                   # x-ratelimit-limit-requests header
TPM_DEV, RPM_DEV = 250_000, 1_000  # Groq's published Developer-plan figures for these models: DOCUMENTED, NOT measured on this key
LOCAL_SATURATION_RPS = 6.9         # evidence/phase10/load_results.json, concurrency 2-4, scripted model
MODEL_LATENCY_S = 0.76             # generate stage p50, n=19, evidence/phase10/latency_analysis.json
REQUEST_LATENCY_S = 0.95           # request p50 with model, n=19
TRACE_BYTES = 9_527                # mean span bytes per request, 45 golden requests (sum of both span files per trace id); 8.9 KB single-version, 10.7 KB comparison
SCAN_S_PER_REQUEST = 1.5 / 30_000  # trace-query scan, Phase 7 synthetic measurement (phase7.md)

# ---- ASSUMPTIONS (the brief gives ~9,000 active integrators and no usage rate; these are illustrative, not measured)
INTEGRATORS = 9_000
BUSY_HOUR_SHARE = 0.10             # share of a day's requests that fall in the busiest hour
PEAK_FACTOR = 3.0                  # peak second vs the busy-hour average

caps = {
    "free key, tokens/day (measured)": TPD_FREE / TOKENS_PER_QUERY / 86400,
    "free key, tokens/minute (measured)": TPM_FREE / TOKENS_PER_QUERY / 60,
    "paid plan, tokens/minute (documented)": TPM_DEV / TOKENS_PER_QUERY / 60,
    "paid plan, requests/minute (documented)": RPM_DEV / 60,
    "local engine, one process, scripted model (measured)": LOCAL_SATURATION_RPS,
}
print("sustained capacity in requests/second at 1,836 tokens per query:")
for k, v in caps.items():
    print(f"  {k:55} {v:8.3f} req/s  = {v * 86400:12,.0f} per day if sustained")
print(f"\nfree key: {TPD_FREE / TOKENS_PER_QUERY:.0f} queries/day at the mean token cost\n")

print("| queries per integrator per day | requests/day | busy-hour avg req/s | peak req/s | tokens/day | USD/day | USD/30 days | trace GB/day | scan of 1 day (s) |")
print("|---|---|---|---|---|---|---|---|---|")
rows = []
for scale, label in ((1, "baseline"), (10, "10x")):
    for q in (1, 3, 10):
        per_day = INTEGRATORS * q * scale
        avg = per_day * BUSY_HOUR_SHARE / 3600
        peak = avg * PEAK_FACTOR
        rows.append((q, scale, per_day, avg, peak))
        print(f"| {q} ({label}) | {per_day:,} | {avg:.2f} | {peak:.2f} | {per_day * TOKENS_PER_QUERY / 1e6:.1f}M | {per_day * COST_PER_QUERY:,.0f} | {per_day * COST_PER_QUERY * 30:,.0f} | "
              f"{per_day * TRACE_BYTES / 1e9:.2f} | {per_day * SCAN_S_PER_REQUEST:.0f} |")

print("\nfactor by which each scenario exceeds each capacity (peak req/s / capacity):")
print("| scenario | vs free-key tokens/day | vs paid tokens/minute | vs local engine |")
print("|---|---|---|---|")
tpd_rps = TPD_FREE / TOKENS_PER_QUERY / 86400
for q, scale, per_day, avg, peak in rows:
    print(f"| {q}/day x{scale}: {per_day:,} req/day, avg {avg:.2f}, peak {peak:.2f} req/s | {per_day / (TPD_FREE / TOKENS_PER_QUERY):,.0f}x (per day) | {peak / caps['paid plan, tokens/minute (documented)']:.2f}x | {peak / LOCAL_SATURATION_RPS:.2f}x |")

print("\nin-flight requests by Little's law (rate x request latency 0.95 s with the model):")
for q, scale, per_day, avg, peak in rows:
    print(f"  {q}/day x{scale}: average {avg * REQUEST_LATENCY_S:.1f}, peak {peak * REQUEST_LATENCY_S:.1f}")
