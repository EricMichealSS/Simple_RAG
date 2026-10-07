# Phase 7: Verdict

**Done:** 2026-10-05. Output: `verdict.md` (7 lines, sunk-cost bias named first).
Pre-registered rule (protocol.md, D9): KEEP only if (1) squad beats single by >= 1 pass, (2) cost and p50 each <= 2x single, (3) failure test not "lied". Result: (1) tie 7/7 vs 7/7 -> fails; (2) cost 1.3x -> passes (latency not usable); (3) lied -> fails. **KILL.** The result is robust to the excluded `hard_11` run: counted as-run (7 vs 8 passes) rule 3 still fails.
Prediction scorecard (prediction.txt): multiplier 2-4x -> actual 1.2x (wrong); single wins on cost -> yes (1.3x cheaper); pass rates within one question -> yes; biggest share = manager re-read -> no (worker's own tool loop); failure: degrade or lie, not retry -> it retried, then lied (partly right).
