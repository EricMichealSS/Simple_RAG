# Week 10: Complete Guide
**A team of agents vs our single agent. Track E: Developer Documentation (GitHub API + Swagger Codegen)**
Everything about Week 10 in one file: what it is, how it works, every file, the results, what went wrong, how to demo it to your mentor, and the questions asked along the way.

---

## 0. The whole thing in 30 seconds
- **Question we answered:** "Is a manager + 2 specialist agents better than the single agent we already have, or does it just cost more?"
- **What we built:** an orchestrator (manager) that delegates to a *version/deprecation worker* and a *code-sample worker*, plus the measuring tools to race it fairly against the single agent.
- **What we found:** on the cases both finished, accuracy tied (7/7 vs 7/7), the squad used **1.2x the tokens** (25,129 vs 21,118) and **1.3x the cost** ($0.00040 vs $0.00030 per question). When the version worker was made to return an HTTP 500, the squad **retried, then gave a confident wrong answer without a verified version check (it "lied")**.
- **Verdict:** **KILL** (keep the single agent), decided by a rule written *before* the race.
- **Honest limits:** 1 repeat on 7 cases, not 3 repeats on 10 (free-tier token limits). Latency is not used (a measurement problem on my side). Details in section 10.

---

## 1. What Week 10 is about (plain English)
"Multi-agent" means several AI agents working as a team instead of one AI doing everything. It is very fashionable, so this week we did the boring, honest thing: build a small team, **race it against the single agent we already had, and keep whichever one really wins**. The marks are for the numbers and the honesty, not for the team winning. "We tried it and it lost" is a good result.

Our team (the classic "manager plus specialists" shape):
- **Orchestrator (manager):** reads the question, delegates, writes the final answer. It has **no documentation tools of its own**.
- **Version/deprecation worker:** finds what changed or was deprecated between versions. Tools: `search_docs`, `check_deprecation`.
- **Code-sample worker:** reads the current spec for one version + endpoint and reports parameters and a sample. Tool: `get_openapi_spec` only.

### Two mismatches between the task text and our project (tell your mentor)
1. The brief says "last week you built an orchestrator". In our timeline Week 9 was MCP, so **no orchestrator existed; we built it this week**.
2. The brief says "the same 10 Week-6 eval cases". Week 6's cases are about an older document set that the agent's tools cannot answer. We used the **10 race questions the single agent was already measured on in Week 7** (`agent_race/questions.py`) for both arms. The real rule behind that sentence is "don't change the ruler between the two arms", and we followed it.

---

## 2. Concepts you learned this week
| Concept | Plain meaning | Where it shows up here |
|---|---|---|
| **Orchestrator-worker pattern** | A manager agent splits a job and hands pieces to worker agents. | `orchestrator.py` + `workers.py` |
| **Specialist agent** | A worker with a narrow prompt and only the tools it needs. | Version worker (2 tools), code worker (1 tool) |
| **Hand-off** | One agent passing work to another. | Every manager-to-worker and worker-to-manager message |
| **Context re-send** | Each time work is handed over, the receiver needs context, so facts get copied into several prompts and billed again each time. | The token counts per hand-off |
| **Context re-send multiplier** | Squad total tokens divided by single-agent total tokens. | **1.2x** |
| **Fair race** | Same cases, model, judge, ceilings; arms interleaved; repeats; no re-runs after seeing results. | `protocol.md` |
| **Failure behaviours** | When a worker breaks: *retried*, *degraded* (honest partial answer), or *lied* (confident answer with no verified check). | `failure_case.md` |
| **Sunk-cost bias** | Keeping something because you spent time building it. | Named first in `verdict.md` |
| **A2A (Agent-to-Agent protocol)** | Open standard for agents to hand work to other agents: an AgentCard describes an agent; tasks have states (submitted, working, completed, failed, canceled, rejected, input-required, auth-required). | Bonus files |
| **MCP vs A2A** | MCP connects an agent to *tools*; A2A connects an agent to *other agents*. | Week 9 vs Week 10 |
| **CrewAI / AutoGen** | Ready-made multi-agent frameworks. We hand-built instead, because the experiment is about seeing and counting every hand-off. | Not used |

---

## 3. Which models were used
| Role | Model | Settings |
|---|---|---|
| Single agent, manager, version worker, code worker (final race) | `openai/gpt-oss-20b` | temperature 0, max 1024 output tokens per call |
| Judge (PASS/FAIL vs the golden answer) | `openai/gpt-oss-120b` | same `grader.py` and prompt as Week 7; never the model that wrote the answer |
| Failure test (the injected 500) | `openai/gpt-oss-120b` | the 20b daily quota was used up at that time |
| First, void race attempt | `openai/gpt-oss-120b` | thrown away, see section 10 |

Price used for cost: gpt-oss-20b $0.075 / 1M input tokens, $0.30 / 1M output tokens (`pricing.py`). Provider: Groq, free tier.
**Why the same model for every role:** if we changed the model and the pattern together, we could not tell which one caused any difference. A cheaper model for the workers is a follow-up idea, not part of this test.

---

## 4. How the squad works

```
                    developer question
                           |
                 +---------v---------+
                 |   ORCHESTRATOR    |   LLM loop. Its only "tools" are the two workers.
                 +----+---------+----+
   delegate:            |         |           delegate:
   request +            |         |           request +
   api_version +        |         |           api_version +
   endpoint             |         |           endpoint
        +---------------v--+   +--v-----------------+
        | VERSION/DEPRECA- |   | CODE-SAMPLE WORKER |
        | TION WORKER      |   | tool: get_openapi_ |
        | tools: search_   |   |       spec         |
        | docs, check_dep- |   +--------------------+
        | recation         |
        +------------------+
   each worker returns {"status": 200, "body": "short text report"}
```

### Real example (recorded run, question `hard_04`, squad arm, 3,556 tokens)
| # | Who | Hand-off (what triggered the model call) | Prompt tokens | Output tokens |
|---|---|---|---|---|
| 1 | orchestrator | user -> orchestrator | 659 | 105 |
| 2 | version_worker | orchestrator -> version_worker (task in) | 553 | 215 |
| 3 | version_worker | version_worker tool-loop lap 2 | 705 | 303 |
| 4 | orchestrator | version_worker -> orchestrator (results read) | 815 | 201 |

1. The manager reads the question and decides it needs a version check, so it calls `ask_version_worker` with the request plus `api_version` and `endpoint`.
2. The worker starts with a fresh, small prompt and calls `check_deprecation`.
3. The worker's second call carries the document text it just fetched; it writes a short report.
4. The manager reads that short report and writes `ANSWER: ...`.

The single agent solved the same question in 2 calls (3,413 tokens): it searched and answered, with no hand-offs.

---

## 5. How context passes between agents, and how the "re-send" is handled
This is the part mentors probe, so here are the exact rules as coded.

### Manager to worker (what the worker receives)
A **brand-new conversation**: the worker's own system prompt plus a task made of the manager's `request` text, with `api_version:` and `endpoint:` lines appended. **The manager's history is not passed down.** Appending the version explicitly means the target version cannot be lost in the hand-off (a classic bug: a v2 sample for a v3 question).

### Worker to manager (what comes back)
Only the worker's **final text**, wrapped as `{"status": 200, "body": "..."}`. The raw documentation text the worker fetched stays inside the worker and is never sent up. A failure comes back as `{"status": 500, "body": {...}}`, and the manager receives it as data like any other result.

### The manager's own context
The manager keeps its whole message history, and **every lap re-sends all of it** (system prompt, question, its own tool calls, each worker report). That is normal for any ReAct loop; the squad does not change it.

### What we did and did NOT do about the re-send cost
- **Did (by design):** minimal task down (no history), summary up (no raw documents), tools split so each worker's prompt is small.
- **Did (measurement):** every model call is labelled with the hand-off that triggered it, and the tokens are added up per label, so the cost of each hand-off is visible.
- **Did NOT:** build any compression, summarising or history-trimming mechanism. We measured the re-send; we did not engineer it away. (Week 7 had an experimental "compact history" flag for the single agent; it is not used here.)

### What the measurement showed
| Hand-off | Tokens | Share of squad tokens |
|---|---|---|
| version_worker tool-loop (lap 2+) | 7,718 | **30.7%** (largest) |
| user -> orchestrator | 5,858 | 23.3% |
| version_worker -> orchestrator (results read) | 5,245 | 20.9% |
| orchestrator -> version_worker (task in) | 4,213 | 16.8% |
| code_worker, all three hand-offs together | 2,095 | 8.3% |

**Multiplier line (from `multiplier.txt`):** `MULTIPLIER: 1.2x (squad 25,129 / single 21,118 tokens ...). Largest hand-off: "version_worker tool-loop (lap 2+)", 31% of all squad tokens.`

Reading it: the biggest cost is the **worker re-reading the document text it just retrieved** (its lap-2 call carries the whole tool result), not the manager re-reading worker output (20.9%). My written prediction said the manager re-read would be biggest, and **that prediction was wrong**. Also, 6 of 7 delegations went to the version worker: most questions are look-ups, so the code-sample worker was barely used.

---

## 6. Safety limits and the HTTP-shaped contract
- **Ceilings, identical for both arms (per question):** 60,000 tokens, $0.05, 120 s of wall-clock, 8 laps per agent loop, at most 6 delegations. (Week 7 used an 8,000-token cap; a squad cannot fit under that, so both arms got the same higher ceiling.)
- **Worker contract:** `{"status": <int>, "body": ...}`; status 400 or above means a failed call, as over a real network. Workers run **in-process** (same Python program) so both arms share one code path. Limitation: this hides real network latency between agents.
- **Rails that are not failure handling:** a 30 s timeout per worker call and the delegation cap. The manager has **no failure-handling code and no failure instruction in its prompt**; what it does on a 500 is exactly what we wanted to measure.

---

## 7. Every file, one by one
All new code is in `agent_race/squad/`. Existing files were reused untouched except one tiny change to `pricing.py`.

### Rules and plan (written BEFORE the race)
| File | What it is and why |
|---|---|
| `agent_race/week10_plan.md` | The plan: concepts, design, phases, edge cases, decisions D1-D11. |
| `squad/protocol.md` | The frozen rulebook: same cases/judge/model/ceilings, interleaving, the retry/degrade/lie definitions, and the **keep/kill rule**. Has two dated amendments, A1 (model switch after the quota problem) and A2 (provider 413 errors counted as failed runs). |
| `squad/prediction.txt` | A dated prediction written before running (multiplier 2-4x, single wins cost, passes within one question, biggest share = manager re-read). Mostly wrong, which is useful to show. |

### The code
| File | What it does, in plain terms |
|---|---|
| `squad/metering.py` | The **measuring tape.** `metered_chat()` is the only way the squad calls the model, and it writes one JSON line per call (trace id, who called, hand-off label, prompt/completion tokens, cost, latency). It retries provider rate limits and keeps that waiting time out of the latency clock. `Tracer` collects the calls of one run. `trace_from_single()` rebuilds the same kind of record for the single agent from the log it already returns, so `agent.py` did not need to change. |
| `squad/workers.py` | The two specialists: their prompts, their tool subsets, the small loop each runs (max 8 laps, 30 s timeout), the `{status, body}` result, and the **fault-injection switch** (`set_fault`) used for the 500 test. A simulated 500 costs no tokens, like a real down service. |
| `squad/orchestrator.py` | The **manager loop.** Defines the two delegation "tools", the manager's prompt (it lists the known versions and endpoints and demands `api_version` + `endpoint` on every delegation), enforces the ceilings, labels every call with its hand-off, and returns the same result shape as the single agent plus a trace. `python3 orchestrator.py "question"` runs one question. |
| `squad/run_squad_race.py` | The **race runner.** For each question runs both arms (alternating who goes first), grades each answer with the judge, saves every run to `race_runs.json`, and can **resume** without repeating finished runs. Wraps the unmodified single agent and judge so provider rate limits are retried. |
| `squad/analyze.py` | Turns the recorded runs into `race_table.md`, `handoffs.log` and `multiplier.txt`. It **asserts that the per-hand-off tokens add up exactly to each arm's total.** It also applies the objective rule that excludes runs cut off by rate-limit waiting. |
| `squad/fault_injection.py` | The failure test: on `hard_04`, makes the version worker return 500 (persistent, then transient) and records what the manager did. |
| `squad/demo_replay.py` | **Offline demo** (no API, no quota): replays the recorded runs. |
| `squad/demo_live.py` | **Live demo:** real model calls, shows delegation, hand-off tokens and the injected 500. |

### The deliverables the task asked for
| File | Rubric item |
|---|---|
| `squad/race_table.md` | 4 metrics x 2 arms (30 pts) |
| `squad/handoffs.log` + `squad/multiplier.txt` | per-hand-off tokens (10 pts), multiplier + named hand-off (25 pts) |
| `squad/failure_case.md` | injected 500, retry/degrade/lie (20 pts) |
| `squad/verdict.md` | keep/kill, 2+ numbers, sunk-cost named (15 pts) |
| `squad/agent_card.json`, `squad/a2a_notes.md` | bonus |

### Raw data and notes
| File | What |
|---|---|
| `squad/race_runs.json` | one row per finished run (answer, verdict, tokens, cost, time) |
| `squad/traces.jsonl` | one line per model call; everything is generated from this |
| `squad/failure_runs.json`, `squad/traces_failure.jsonl` | the failure test |
| `squad/race_stdout.log` | runner console output |
| `squad/PHASE0...PHASE8 *.md`, `squad/week10_report.md` | per-phase notes and the short report |
| `squad/void_attempt1_quota/` | the failed first attempt, kept as evidence (README inside) |

### Existing files reused
`agent.py` (the single agent, **untouched**), `tools.py` (the 3 docs tools, shared by both arms), `questions.py` (the 10 questions), `grader.py` (the judge, untouched). **One change:** `pricing.py` now reads an optional `RACE_MODEL` environment variable (default unchanged), so the same code can run on either model.

---

## 8. How the race was run (the fairness rules)
1. **Same ruler:** the same 10 questions, same golden answers, same judge for both arms.
2. **Single arm = `agent.py` exactly as it was**, with its 3 docs tools (not the 6-tool MCP agent, whose extra tool descriptions would have inflated its tokens).
3. **Same model, temperature and ceilings** for every role.
4. **Interleaved:** question by question, both arms back to back, alternating who goes first, so slow API minutes hit both.
5. **No re-running after seeing results.** Only infrastructure errors (rate limits, provider errors) are retried and logged.
6. **Keep/kill rule fixed in advance** (in `protocol.md`): KEEP only if all three hold:
   - the squad beats the single agent by at least 1 passed question;
   - squad cost per question and p50 latency are each at most 2x the single agent;
   - the failure test does not end in "lied".
   A tie on accuracy means KILL, because there is nothing to pay for.
7. **Counting:** tokens are what the API reported for every call, summed; the judge's tokens are excluded from both arms.

---

## 9. Results

### Race table (7 cases both arms completed, 1 repeat)
| Metric | Single agent | Squad |
|---|---|---|
| Pass count | 7/7 | 7/7 |
| Total tokens (7 questions) | 21,118 | 25,129 |
| Cost per question | $0.00030 | $0.00040 |
| p50 / p99 latency | 14.5 s / 35.8 s | 2.5 s / 3.2 s **(not usable, see section 10)** |

### Per question
| Case | Single tokens | Squad tokens | Both passed? |
|---|---|---|---|
| easy_01 | 2,009 | 2,952 | yes |
| easy_02 | 3,039 | 4,353 | yes |
| medium_01 | 1,731 | 3,066 | yes |
| medium_07 | 2,894 | 4,185 | yes |
| hard_01 | 2,990 | 3,397 | yes |
| hard_04 | 3,413 | 3,556 | yes |
| hard_06 | 5,042 | 3,620 | yes |

The squad was cheaper only on `hard_06`; on every other case the hand-offs were pure overhead. These are mostly look-up questions with nothing to parallelise.

### The failure test (`hard_04`, version worker returns 500)
| | Persistent 500 (every call) | Transient 500 (first call only) |
|---|---|---|
| Version-worker calls | 7: six 500s, then my delegation cap blocked the 7th | 2: a 500, then a 200 |
| Final answer | "did not change in any functional way..." (wrong) | correct (settable at creation from 2025-06-01) |
| Said the version check failed? | **No** | n/a |
| Behaviour | **retried, then lied** | **retried and recovered** |
| Tokens | 9,091 | 4,204 |

The manager has no failure handling, so it retried blindly until the cap stopped it and then wrote a fluent, confident, unverified and wrong answer. The failure is silent: the answer looks like any other. (These runs used gpt-oss-120b, one run each.) **On 2026-10-07 the live demo on gpt-oss-20b showed the same pattern again:** the manager hit the dead worker 7 times (six 500s, then the cap) and still produced a confident answer.

**Production fix (recommended, deliberately not built; Week 10 measures, it does not patch):** bounded retry with backoff on 5xx only; then an explicit "version check unavailable" result that the manager must pass on (for example a required `verified_version` field); a circuit breaker; and an alert on any answer produced while a worker was down.

### Verdict (`verdict.md`, 7 lines)
**KILL for now.** Rule 1 fails (tie, no accuracy gain), rule 2 passes on cost (1.3x), rule 3 fails (lied). Sunk-cost bias is named first: building the squad is not a reason to keep it. It would be reconsidered with an explicit "unverified" flag, bounded retry, a paid-tier rerun with 3 repeats, and a task with independent sub-questions to parallelise.

### Prediction scorecard
| Predicted before the race | What happened |
|---|---|
| Multiplier 2-4x | 1.2x (wrong; on easy look-ups the overhead is small) |
| Single agent wins on cost | Yes (squad 1.3x the cost) |
| Pass rates within one question | Yes (7/7 vs 7/7) |
| Biggest share = manager re-reading worker results | No: the worker's own re-read of docs (30.7%) |
| Failure: degrade or lie, not retry | It retried first, then lied (partly right) |

---

## 10. What went wrong along the way (say this to your mentor first)
This section is the most valuable part for a senior reviewer: the measuring setup was the biggest risk, and I found and disclosed my own errors.

1. **The first race attempt was void.** Groq's free tier allows `gpt-oss-120b` only 200,000 tokens per day (and 8,000 per minute). The attempt used about 228k tokens and 29 of 60 runs ended in rate-limit errors with no answer. A bug in **my squad tool schema** (optional fields; the model sent `endpoint: null`, which the provider rejected) caused 2 more failures. No conclusion was drawn; the files are kept in `void_attempt1_quota/`.
2. **Re-run on `gpt-oss-20b` for both arms** (judge stays 120b). Written down as protocol amendment A1 before any valid result existed.
3. **My wait-time parser bug:** "try again in 75ms" was read as 75 *minutes*, which stopped the runner. Fixed (tested on five message formats).
4. **Provider 413 "request too large":** on the two *negative* cases (`easy_13`, `medium_13`) the squad's manager kept delegating, its context grew to about 7,400 tokens and crossed the provider's 8,000-token-per-request cap (amendment A2).
5. **The daily quota ran out again,** so the squad's `easy_13` and `medium_13` runs never finished (about 28,000 tokens each before stopping). They are **not counted as pass or fail.** The single agent also spent 22,000-26,000 tokens on those cases and timed out, so negative cases are expensive for both arms.
6. **`hard_11` excluded by an objective rule.** `agent.py` measures its 120 s ceiling on raw clock time, including my rate-limit waits, so the single run was cut off after 2.4 s of real work. The rule: a wall-clock budget hit with under 120 s of active time is a runner artifact. Counted as run it would be 7 vs 8 passes; the keep/kill outcome is the same either way (rule 3 fails regardless).
7. **Latency is not used.** The single arm's latency still contained hidden retry sleeps from the SDK, while the squad's client had retries off. The squad looking 5x faster is a measurement artifact, not a result.
8. **Evidence is weak:** 1 repeat (not 3), 7 paired cases, so no spread and p99 is just the slowest question. It all points one way, but the report says plainly that this is weak evidence.
9. **The failure test ran on 120b, one run per variant,** with a simulated in-process 500 (a real remote failure would also add latency).

**Lesson:** audit the measuring harness as hard as the system under test. Most of the unfairness risk was in my own measurement code.

---

## 11. Bonus: AgentCard and A2A
- **`squad/agent_card.json`:** an illustrative AgentCard for the orchestrator (name, description, version, 2 skills, text input/output modes, HTTP+JSON interface, bearer auth). It is not served anywhere, and the well-known URL path is intentionally not asserted because I could not verify it.
- **Where the failed case ends in the A2A lifecycle:** it should end **`failed`**, not `input-required`. `input-required` is for when the *user* can supply what is missing, and `hard_04` already names both versions. A 500 is a server fault. The real bug is that our squad finished as `completed` with a wrong answer, claiming success after a failed sub-task. `input-required` would be right for a question that names no version ("is this deprecated?"), where the worker truly needs "which SDK version are you on?".
- **What A2A buys over a plain REST call (2 lines):** a standard, discoverable contract (AgentCard skills/modes/auth and defined task states, so "worker down" is distinguishable from "needs more info"); and built-in support for long-running work and structured hand-offs (task ids, status, messages, artifacts) where a bare REST call only returns a body or an error code.

---

## 12. How to show your mentor it really works

### Option A: offline replay (always works, no API key, no quota)
```bash
cd /Users/softsuave/Sam_RAG/developer-rag
python3 agent_race/squad/demo_replay.py                  # default case hard_04
python3 agent_race/squad/demo_replay.py --case medium_07 # any paired case
python3 agent_race/squad/demo_replay.py --list           # list cases
```
It prints, from the **real recorded runs**: one question through both arms with every model call and hand-off; the token share per hand-off; the race table and multiplier; the failure test; and the verdict.

### Option B: live demo (real model calls, a few thousand tokens)
```bash
cd /Users/softsuave/Sam_RAG/developer-rag
source .venv/bin/activate
python3 agent_race/squad/demo_live.py                 # single vs squad, then the injected 500
python3 agent_race/squad/demo_live.py --no-fault      # skip the 500 part
python3 agent_race/squad/demo_live.py --question "What Maven group id is used to download the Swagger Codegen CLI jar for version 3.x?"
```
Needs the venv and `.env` with the API key. It was tested on 2026-10-07 and works. Model output varies a little run to run, so numbers will not match the recorded ones exactly. If the free-tier quota is exhausted it will wait or fail; then use Option A.

### Option C: run just the squad on one question
```bash
cd /Users/softsuave/Sam_RAG/developer-rag/agent_race
RACE_MODEL=openai/gpt-oss-20b python3 squad/orchestrator.py "your question"
```

### A 5-minute demo script
1. **(30 s) The question.** "Does a manager + 2 workers beat our single agent, or just cost more?" Show the diagram in section 4.
2. **(60 s) Live demo, healthy part.** Run `demo_live.py`. Point at: the manager calls `ask_version_worker` and passes `api_version` + `endpoint`; the worker gets a fresh small prompt; the hand-off table shows tokens per hand-off; the single agent needed 2 calls, the squad 4.
3. **(60 s) The 500.** Show the persistent-500 part: the manager calls the dead worker again and again, then answers confidently with no verified version check. "That is what 'lied' means."
4. **(60 s) The numbers.** Open `race_table.md`, `handoffs.log` and `multiplier.txt`: 1.2x, 30.7% for the worker re-reading docs, 7/7 vs 7/7.
5. **(60 s) The verdict and the honesty.** Open `verdict.md` (sunk-cost named first, KILL), then the "what went wrong" list: void first attempt, excluded cases, latency not usable, 1 repeat.
6. **(30 s) Close.** "The finding is a decision backed by numbers: keep the single agent, and here is exactly what would change that."

Do not re-run `run_squad_race.py` before the demo: it resumes from `race_runs.json` and would add new runs to the submitted data.

### Re-running everything (only if asked)
```bash
cd /Users/softsuave/Sam_RAG/developer-rag/agent_race
RACE_MODEL=openai/gpt-oss-20b JUDGE_MODEL=openai/gpt-oss-120b RACE_REPEATS=3 python3 squad/run_squad_race.py   # resumable; needs daily quota (move race_runs.json/traces.jsonl aside first for a fresh race)
python3 squad/analyze.py                                    # regenerates race_table.md, handoffs.log, multiplier.txt
RACE_MODEL=openai/gpt-oss-120b python3 squad/fault_injection.py   # the 500 test
```
On a paid tier a full 3-repeat race should complete without the quota problems.

---

## 13. Questions your mentor may ask, with answers
**Q: How do the agents pass context to each other?**
The manager sends a worker only a request plus `api_version` and `endpoint`; the worker starts a fresh conversation. The worker sends back only a short text report. The raw docs stay inside the worker. The manager's own history grows normally and is re-sent each lap.

**Q: How did you handle the context re-send?**
By design (minimal task down, summary up, split tools) and by measurement (every call is labelled with its hand-off and tokens are summed per label, with a check that they add up to the totals). I did not build a compression mechanism. The biggest share was the worker re-reading docs (30.7%), not the manager re-reading reports (20.9%).

**Q: Why did the team lose?**
These questions are mostly look-ups with one delegation each, so there was nothing to parallelise and the hand-offs were overhead (1.2x tokens, 1.3x cost for no accuracy gain). And without failure handling the manager is unsafe when a worker dies.

**Q: Is one repeat on 7 cases enough?**
No, and the report says so. It was limited by free-tier token quotas. The direction is consistent across the numbers, and the report lists what would change the verdict (paid tier, 3 repeats, an "unverified" flag, bounded retry, a task with independent sub-questions).

**Q: Why is the squad faster in your table?**
It is not a real win. The single arm's latency included hidden SDK retry sleeps while the squad's did not, and workers run in-process. I did not use latency in the verdict.

**Q: Why not use CrewAI or AutoGen?**
The experiment is about seeing and counting every hand-off; a framework can hide that. They would also likely need a newer Python than our 3.9 environment.

**Q: What is the difference between MCP and A2A?**
MCP connects an agent to tools or data sources (a function you call). A2A connects an agent to another agent (an autonomous party with a task lifecycle that may take time or ask a question back).

**Q: Did you change the single agent?**
No. `agent.py`, `tools.py`, `questions.py` and `grader.py` are untouched. The only edit to existing code is the optional `RACE_MODEL` setting in `pricing.py`.

**Q: What would you do next?**
Add an explicit "unverified" flag to the worker contract, bounded retry with a circuit breaker, rerun with 3 repeats on a paid tier including the two negative cases, and test a task that really has independent parts.

---

## 14. Record of the questions asked while building this, and the answers
- **"What are the Week 10 task and plan?"** The task analysis and plan are in `agent_race/week10_plan.md`. Decisions were taken from a production-ready point of view (D1-D11), including a pre-registered keep/kill rule.
- **"Is it still running? What are you doing now? Is that run done?"** The 60-run race took far longer than planned. A rate limit and then the daily token quota slowed it, and it stopped several times (partly because of my own wait-parsing bug). Final state: 1 repeat, 18 of 20 runs saved, finalized on the 7 paired cases.
- **"Which model, how does multi-agent work, how is context passed, how is re-send handled, explain each file."** Sections 3, 4, 5 and 7 of this guide.
- **"How can I show my mentor I implemented this? Can I run something?"** Section 12: the offline replay and the live demo, with a 5-minute script.

---

## 15. Git: what to push
**Push:** everything in `agent_race/squad/` (including `void_attempt1_quota/`), `agent_race/week10_plan.md`, `agent_race/week10_complete_guide.md`, and the one-line change in `agent_race/pricing.py`.
**Do not push:** `.env` (API key), `data/` (Chroma index), virtual environments, `__pycache__`. I did not run any git command; that is yours.

---

## 16. Glossary
- **ReAct loop:** think, call a tool, read the result, repeat until you can answer.
- **Token:** the unit the model reads and writes; cost is per token.
- **Ceiling / budget:** a safety limit (tokens, dollars, seconds, laps) that stops a runaway loop.
- **Golden answer / judge:** the known-correct answer, and a model that marks an answer PASS or FAIL against it.
- **Trace:** the recorded list of every model call in one run.
- **p50 / p99:** the median latency and the near-worst latency; with 10 samples p99 is just the slowest run.
- **Interleaved:** running both arms alternately question by question so conditions are shared.
- **HTTP 500 / 429 / 413:** server error / rate limited / request too large.
