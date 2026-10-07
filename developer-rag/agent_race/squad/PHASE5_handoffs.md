# Phase 5: Hand-off analysis

**Done:** 2026-10-05. Code: `analyze.py`. Output: `handoffs.log`, `multiplier.txt`.

```
MULTIPLIER: 1.2x (squad 25,129 / single 21,118 tokens, 7 paired cases, 1 repeat).
Largest hand-off: "version_worker tool-loop (lap 2+)", 31% of all squad tokens.
```
| Hand-off | Tokens | Share |
|---|---|---|
| version_worker tool-loop (lap 2+) | 7,718 | 30.7% |
| user -> orchestrator | 5,858 | 23.3% |
| version_worker -> orchestrator (results read) | 5,245 | 20.9% |
| orchestrator -> version_worker (task in) | 4,213 | 16.8% |
| code_worker (3 hand-offs together) | 2,095 | 8.3% |

## Reading it
- The biggest share is the worker re-reading the docs text it just retrieved (its lap-2 call carries the whole tool result), NOT the manager re-reading worker output. Workers return short summaries, which keeps the manager's re-send small (20.9%). My prediction (manager re-read biggest) was wrong.
- Only 1 of 7 delegations went to the code worker: most questions are look-ups, so the code-sample worker was barely used.
- The 1.2x multiplier is low partly because the cases compared are easy look-ups with one delegation each. The two negative cases (excluded, squad unfinished) were the expensive ones for both arms (squad ~28k tokens each before stopping; single ~22k and ~26k). Do not generalise 1.2x.
