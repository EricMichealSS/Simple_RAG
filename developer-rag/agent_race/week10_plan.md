# Week 10 Plan — A Team of Agents vs. Our Single Agent
**Track E: Developer Documentation (GitHub API + Swagger Codegen migration)**
**Planning only — nothing in this document has been built yet.**

---

## 1. What Week 10 is about (plain English)

"Multi-agent" means several AI agents working together as a team instead of one AI doing everything. It is the most hyped idea in AI right now, so this week we do the boring, honest thing: **build a small team, race it against the single agent we already have, and keep whichever one actually wins.**

Our team is the classic "manager plus specialists" shape:

- **Orchestrator (the manager):** reads the developer's question, splits it into pieces, hands the pieces out, and writes the final answer.
- **Version/deprecation worker (specialist 1):** only knows how to find out what changed or was deprecated between API versions.
- **Code-sample worker (specialist 2):** only knows how to read the current spec and write a correct, version-accurate sample.

The brief says out loud that the single agent often wins, and that this is a *valuable* result. The grade is for the **numbers and the honesty**, not for the team winning.

### Two things in the task text that don't match our project history (worth telling your mentor)

1. **"You spent last week building an orchestrator…"** — we didn't. In our timeline, last week (Week 9) was MCP. So this week has *two* jobs: **build** the squad, then **race** it. The Word brief says exactly that ("Build a small team… and race it"), so we follow the Word brief.
2. **"The same 10 Week-6 eval cases"** — Week 6's cases (`eval/cases.json`, 26 questions) ask about the *older* docs collection (the OctoKit SDK pages). Our single agent from Weeks 7–8 answers over the *newer* docs (GitHub 2022-11-28 vs 2025-06-01 and Swagger v2 vs v3). Those two sets don't overlap, and the agent's tools can't answer the Week-6 questions at all. The 10 cases our single agent was *actually built and measured on* are in `agent_race/questions.py`. **We recommend using those 10** (see Decision D1) and saying so openly. The real rule behind the sentence is "don't change the ruler between the two arms" — and that we will follow strictly.

---

## 2. How this week is different from the earlier weeks

| Week | Question it answered | What changed in the system |
|---|---|---|
| 5 | Where does our RAG fail? | Read traces, named failure modes |
| 6 | Can we trust our judge? | Eval set, assertions, judge validated against a human |
| 7 | Does this task need an *agent* at all? | Built an agent loop + a fixed workflow, raced them |
| 8 | Is the agent right for the *right reasons*? | Trajectory eval (score the path, not just the answer) |
| 9 | Can tools plug in without touching the agent? | MCP servers, config-only expansion (3 → 6 tools) |
| **10** | **Does this task need *several* agents?** | **A manager + 2 workers, raced against the single agent** |

Week 10 is the **same experiment as Week 7, one level up**. Week 7 asked "agent or fixed steps?" and the fixed steps won (100% pass, ~10.7k tokens, 6.6 s, $0.00025 per question, versus the agent's 80%, ~49k tokens, 25.9 s, $0.00094). Week 10 asks "one agent or a team of agents?". Week 7's result is a hint (extra moving parts tended to cost more here), but a hint is not a measurement — so we measure again.

Week 9 (MCP) and Week 10 (A2A) are a pair. **MCP connects an agent to *tools*. A2A connects an agent to *other agents*.** More on that in section 4.

---

## 3. Our current system, re-analysed

```
Weeks 1–6   RAG app:  PDFs → chunks → vectors (ChromaDB) → hybrid search (BM25 + vectors)
            → reranker → LLM answer (app.py, rag_hybrid.py)  +  eval/ (cases, judge, assertions)

Week 7      agent_race/:  tools.py (3 tools over the "race_docs" index)
            agent.py  = hand-built agent loop (the SINGLE AGENT we race this week)
            workflow.py = fixed 4-step pipeline        grader.py = judge     pricing.py = cost
            questions.py = the 10 race questions       race.py = the race harness

Week 8      trajectory_eval.py, failure_modes.py — scored the path, found one mitigation + its price

Week 9      agent_race/mcp/ — MCP servers + an MCP-based agent (tools discovered, not imported)

Week 10     agent_race/squad/ — NEW: orchestrator + 2 workers + hand-off metering  (this plan)
```

**What we already measured for the single agent** (so we know what "normal" looks like):

| Run | Pass rate | Total tokens (10 questions) | p50 latency | Cost per question |
|---|---|---|---|---|
| Week 7 race | 0.8 | 49,477 (≈ 4.9k per question) | 25.9 s | $0.00094 |
| Week 8 baseline | 0.8 | 47,650 | 20.1 s | ≈ $0.0007 (median) |

Two lessons from those runs matter *this* week: (a) the same agent gives slightly different numbers on different days, so one run is not enough (we will repeat both arms); (b) the old 8,000-token ceiling killed some single-agent runs (the "no answer" cases) — a team will naturally use more tokens, so the ceiling has to be set fairly for both arms (Decision D4).

**What we reuse, untouched:** `tools.py`, `agent.py`, `questions.py`, `grader.py`, `pricing.py`. **What is new:** everything in a new `agent_race/squad/` folder. We do not edit the single agent at all — editing it would change the ruler.

**Environment:** the existing Python 3.9 venv already has everything (`openai`, `chromadb`, `sentence-transformers`). No new packages, no new venv. (Week 9's Python 3.12 venv was only needed because the `mcp` package needs 3.10+; this week has no such need.)

---

## 4. The concepts we will learn — what, how, why

### 4.1 Orchestrator–worker pattern
**What:** one "manager" agent receives the job, splits it, sends pieces to "worker" agents, collects their results and writes the final answer.
**How it will work in code:** the manager is the *same kind of loop* as our Week 7 agent. The only difference: its "tools" are the two workers. When the manager "calls a tool", we actually run a small worker agent (its own loop, its own prompt, its own few tools) and return the worker's text to the manager.
```python
manager_tools = [ask_version_worker, ask_code_worker]      # workers wearing a tool costume
loop:
    reply = llm(manager_messages, manager_tools)
    if no tool call: return the final ANSWER
    for each delegation in reply:
        result = worker.run(task, context)                 # worker = its own small loop
        log_handoff(manager → worker, token counts)
        manager_messages.append(result)
```
**Why we use it:** it is the standard shape people reach for when one prompt feels "too big". This week we find out if it is worth it.

### 4.2 Specialist agents
**What:** a worker with a *narrow job, a short prompt and only a few tools*.
**Why it might win:** a smaller prompt and fewer tool descriptions mean fewer tokens per call and less room to pick the wrong tool. The brief calls this "the only plausible source of a win."
**Our split:**
- Version/deprecation worker → tools: `search_docs`, `check_deprecation`
- Code-sample worker → tool: `get_openapi_spec` only (it must not get all three tools — that would remove the very advantage we are testing)

### 4.3 Hand-offs and the "context re-send" cost
**What:** every time one agent passes work to another, the receiving agent needs enough context to do the job — so the same facts get copied into several prompts, and each prompt is billed.
**Worked example (made-up numbers, only to show the shape — not measurements):**

```
Single agent, 3 laps:   1,000 + 1,600 + 2,200                               = 4,800 tokens in
Squad (manager + 2 workers + final answer):
  manager plans           900
  version worker        700 + 1,300                                      = 2,000
  manager lap 2        1,250   (now carries the worker's 250-token result)
  code worker           950 + 1,550  (it needs those same facts again)   = 2,500
  manager final lap    1,650   (carries both results)
  total                                                                   = 8,300 tokens in
Multiplier = 8,300 / 4,800 ≈ 1.7×
```
The same 250-token fact appears in three different prompts. That repeated copying is the "re-send" cost. It can also go the other way — if hand-offs are lean and workers return short summaries, a team *can* be cheaper — which is exactly why we measure instead of assuming.

**What we will report:** `Context re-send multiplier = squad total tokens ÷ single-agent total tokens` (one decimal), plus *which* hand-off ate the biggest share, e.g. "manager re-send after worker results: 41% of all tokens."

### 4.4 When multi-agent genuinely helps (and when it doesn't)
Helps when parts of the job are **truly independent** (so they can run in parallel) or need **clearly different instructions/tools**. Hurts when the steps depend on each other (the second worker waits for the first, so latency *adds up*) or when the job is small. Our questions are mostly "look one thing up" — that's a warning sign, and it's part of the final verdict.

### 4.5 A fair race
Same 10 questions, same model, same judge, same tools code, same ceilings, same day, arms interleaved. Four numbers per arm: **pass rate, p50 and p99 latency, total tokens, cost per question.** (Honest note: with only 10 questions, "p99" is basically the single slowest run — we say so in the table rather than pretend it is a smooth statistic.)

### 4.6 What a team does when a worker breaks
We make the version/deprecation worker return an HTTP 500 on one case and watch what the manager does. Three possible behaviours, defined *before* we run:
- **Retried** — it asked the same worker again.
- **Degraded** — it answered, but openly said the version check is missing / the answer is partial.
- **Lied** — it presented version facts as verified when no version check actually succeeded (this is the Week 8 "right answer, wrong path" idea, now applied to a team).
We build the manager *without* any special error handling, so what we record is what it genuinely does, not what we told it to do.

### 4.7 A2A (Agent-to-Agent protocol)
An open standard for one agent to hand work to another agent. It defines how agents describe themselves (an **AgentCard**), how work is tracked (a **task** with a **lifecycle**), and how messages and results are exchanged. It supports three transport styles: JSON-RPC, gRPC and HTTP+JSON/REST.

### 4.8 AgentCard and the task lifecycle
- **AgentCard:** a small JSON "business card": name, description, version, **skills**, supported input/output formats, capabilities (e.g. streaming), and what **authentication** is needed. Other agents read it to discover what this agent can do.
- **Task lifecycle (state names from the A2A spec):** `submitted` → `working` → `completed` (success) · `failed` (error) · `canceled` · `rejected` (agent declined) · plus two "paused" states: **`input-required`** (needs something from the user) and **`auth-required`**.
- **Bonus question we will answer with evidence:** should our injected-500 case end as `failed`, or pause at `input-required` to ask "which SDK version are you on?" Our starting view: a 500 is a *server fault*, not missing user information, so `input-required` is only right if the user could actually supply what's missing — and in our 10 questions the version is always stated. We will confirm or revise after we see the real behaviour.

### 4.9 MCP vs. A2A
| | MCP (Week 9) | A2A (Week 10) |
|---|---|---|
| Connects | an agent ↔ a **tool / data source** | an agent ↔ **another agent** |
| The other side is | a function: you call it, it answers | an autonomous agent: it may think, take a while, ask you a question back |
| Example here | our `docs-search` server | the version/deprecation worker |
Short version: MCP is the USB port for tools; A2A is how two colleagues hand work to each other.

### 4.10 CrewAI / AutoGen
Ready-made frameworks for building multi-agent teams. CrewAI is role-based ("a crew of agents with roles, goals and tasks"); AutoGen is conversation-based (agents talk to each other). Per recent comparison articles, Microsoft has moved AutoGen into maintenance mode and points new projects to its newer Agent Framework. **We hand-build the team instead**, for the same reason as Week 7: the whole experiment is about *seeing every hand-off and counting its tokens*, and a framework can hide exactly that. (They also likely need a newer Python than our 3.9 venv — the same wall we hit in Week 9.)

### 4.11 Sunk-cost bias
The urge to keep something *because we spent time building it*. The verdict file must name this bias out loud before it states keep/kill. Two related rules from the brief: no building a new eval set to "suit" the team, and **no quietly re-running the team until it wins once** — "we tried it and it lost" is the senior result.

### Why all this belongs in our RAG project
Our system grew step by step: retrieve → answer → evaluate → agent with tools → standard tool plumbing → and now "should we split the agent into a team?" Every step added capability *and* cost. The evaluation habits from Weeks 5–8 (same tests, real numbers, honest verdicts) are what let us decide this with evidence instead of fashion.

---

## 5. The squad design

```
                       developer question
                              │
                    ┌─────────▼─────────┐
                    │   ORCHESTRATOR    │  (LLM loop; tools = the two workers)
                    └───┬───────────┬───┘
         delegate task  │           │  delegate task
     ┌──────────────────▼──┐     ┌──▼───────────────────┐
     │ VERSION/DEPRECATION │     │   CODE-SAMPLE WORKER │
     │ WORKER              │     │   tools: get_openapi │
     │ tools: search_docs, │     │          _spec       │
     │        check_depre- │     └──────────────────────┘
     │        cation       │
     └─────────────────────┘
              ▲
     fault injector (Phase 6): returns HTTP 500 on ONE case
```

Guard-rails taken straight from the brief's "common mistakes" list:
- **Don't dump the whole v2 + v3 reference into both workers every hop** → each hand-off carries only what that worker needs (we log payload sizes to prove it).
- **Don't give the code-sample worker every tool** → it gets one.
- **Don't lose the target version in the hand-off** (a v2 sample from a v3 question would be *our* bug, not the pattern's) → the delegation always carries the version; a small check verifies the version string is present in the worker's prompt.
- **Same output format for both arms** (`ANSWER: …`), same model, temperature 0, `max_tokens=1024` on every call (Week 9 lesson: reasoning models burn small budgets on hidden thinking).

---

## 6. Fair-race protocol (written down before anything runs)

1. Same 10 questions, same golden answers, same judge (`grader.py`), same pricing (`pricing.py`).
2. Single arm = `agent.py` exactly as it was raced in Week 7 (its 3 docs tools; *not* the 6-tool MCP agent — extra tool descriptions would inflate its tokens and unfairly flatter the squad).
3. Both arms get the same generous per-question safety ceilings (so the ceiling doesn't decide the winner).
4. Each question is run on both arms **interleaved** (so API slowness at any given minute hits both), **3 repeats per arm**, **all runs reported**, headline = median run. No re-runs after seeing results; only infrastructure errors (e.g. rate limit) get retried, and those are logged separately.
5. A **dated written prediction is saved before the race** (like Weeks 5/6). Draft: "multiplier between 2× and 4×; single agent wins on cost and latency; pass rates within one question of each other; the biggest token share is the manager re-reading worker results."
6. Token/cost counting is identical for both arms: sum of every model call's API-reported usage; the judge's tokens are excluded from both.
7. **Keep/kill rule fixed in advance** (D9) and saved before the race starts.
8. **Reproducibility record** saved with the results: model, temperature, package versions, date, and the exact prompts used (D7, D10).

Estimated run size: 3 repeats × 2 arms × 10 questions ≈ 60 runs, roughly 30–40 minutes, well under $0.50.

---

## 7. Phases (each phase gets its own `.md`, like Week 9)

| Phase | What happens | Produces |
|---|---|---|
| **0 — Protocol & setup** | Write the decisions, definitions (retry/degrade/lie), the **keep/kill rule (D9)**, and the dated prediction; confirm the venv runs; no installs | `squad/protocol.md`, `squad/prediction.txt` |
| **1 — Metering harness** | One shared wrapper that records every model call (who called, which hand-off, prompt/completion tokens, latency) | `squad/metering.py` |
| **2 — The two workers** | Narrow prompts + tool subsets; tested alone, including bad inputs | `squad/workers.py` |
| **3 — The orchestrator** | Manager loop with the workers as its tools; hand-off logging; smoke-test on 2–3 questions | `squad/orchestrator.py` |
| **4 — The race** | Both arms, 10 questions, interleaved, 3 repeats; grade with the same judge | `race_table.md` (4 metrics × 2 arms, same 10 cases named) |
| **5 — Hand-off analysis** | Per-hand-off token counts; compute multiplier; name the biggest share; check the parts add up to the total | `handoffs.log`, multiplier line |
| **6 — Failure injection** | 500 from the version worker on `hard_04` in two variants — **persistent** (headline) and **transient** (first call only); record retried / degraded / lied for each; recommend (not build) the production fix | `failure_case.md` |
| **7 — Verdict** | Keep or kill by applying the pre-agreed rule (D9), ≤ 10 lines, cites ≥ 2 numbers, names sunk-cost bias first | `verdict.md` |
| **8 — Bonus + report** | (Optional) AgentCard + lifecycle mapping + 2 lines "A2A vs plain REST"; final report | `agent_card.json`, `a2a_notes.md`, `week10_report.md` |

Rubric → file map: pass/latency/tokens/cost both arms (30) → `race_table.md` · multiplier + attributed hand-off (25) → multiplier line + `handoffs.log` · injected failure behaviour (20) → `failure_case.md` · verdict (15) → `verdict.md` · hand-off log (10) → `handoffs.log`.

---

## 8. Edge cases we will deliberately check

1. **Rate limits** — a team sends more tokens per minute; the harness retries with backoff and logs it, so a 429 is never miscounted as "the pattern failed".
2. **Run-to-run noise** — repeats; and read by hand any question where the two arms disagree (the judge is an LLM and can be wrong, as Week 6 showed).
3. **Fair ceilings** — confirm no arm dies on a ceiling that the other arm never reaches.
4. **Version lost in a hand-off** — check the delegation text actually contains the target version.
5. **Over-sharing context** — log each payload's size; flag any worker prompt that contains far more than it needs.
6. **Manager that never delegates** (answers from memory) or **delegates forever** — count delegations, cap them.
7. **Numbers that don't add up** — assert that the per-hand-off tokens sum exactly to each arm's total.
8. **Cold start** — one warm-up call before timing, so whichever arm runs first isn't penalised by model loading.
9. **Wrong working directory / paths** (the Week 6 Chroma bug) — all paths resolved absolutely.
10. **The reasoning-token trap** — `max_tokens=1024` on every call.

---

## 9. Files this will produce (none created yet)

| File | Purpose |
|---|---|
| `agent_race/squad/protocol.md`, `prediction.txt` | rules and prediction, fixed before running |
| `agent_race/squad/metering.py` | records every model call |
| `agent_race/squad/workers.py` | the two specialists |
| `agent_race/squad/orchestrator.py` | the manager |
| `agent_race/squad/fault_injection.py` | the "return a 500" wrapper |
| `agent_race/squad/run_squad_race.py` | runs both arms, repeats, grading, writes outputs |
| `race_table.md`, `handoffs.log`, `failure_case.md`, `verdict.md` | the four required deliverables (+ multiplier line) |
| `agent_card.json`, `a2a_notes.md` | bonus |
| `PHASE0…PHASE8 .md`, `week10_report.md` | per-phase notes and the final report |

---

## 10. FINAL decisions (chosen from a production-ready-RAG point of view)

You asked me to decide using the production-ready standard. Each choice below says *why a production team would choose it*. Items marked **CHANGED** differ from my first draft.

- **D1 — Eval cases: the 10 questions in `agent_race/questions.py`.** Production rule: you compare candidates on the *same frozen golden set* the current system was measured on, and you never swap the set to suit a candidate. Week 6's cases test a different corpus the agent can't even reach. Mentor line: *"same 10 cases the single agent was already measured on, same judge, same corpus — nothing changed between arms."*
- **D2 — Single arm: `agent.py`, untouched, default settings, 3 docs tools.** Production rule: the baseline is the thing that exists today, unmodified. (The 6-tool MCP agent would carry extra tool descriptions and flatter the squad.) For context only, we cite Week 7's fixed workflow as the system that beat the single agent — we don't re-run it.
- **D3 — Worker split: version/deprecation worker = {`search_docs`, `check_deprecation`}; code-sample worker = {`get_openapi_spec`}; the manager decides who to call.** Production rule: least privilege — each specialist gets only the tools its job needs, which is also the only fair source of a multi-agent win. Honest side-effect we will report: most of our 10 questions are fact look-ups, so the code-sample worker mostly acts as a spec reader.
- **D4 — Safety ceilings, identical for both arms (CHANGED: now concrete).** Per question: 60,000 tokens, $0.05, 120 s wall-clock, 8 laps per agent loop, at most 6 delegations. Production rule: budgets are a safety net against runaway loops, not a performance limit — so they must be high enough that the ceiling doesn't decide the winner. This replaces Week 7's 8,000-token cap for *both* arms, so this week's single-agent numbers will differ from Week 7's (we disclose that).
- **D5 — Protocol: 3 interleaved repeats per arm; report min / median / max; headline = median run; no re-runs after seeing results.** Production rule: never ship on a single noisy run; show the spread.
- **D6 — Failure test (CHANGED: two variants, not one).** Same case (`hard_04`, where the version check is essential), two runs: **persistent 500** (the required headline: every call to the version worker fails) and **transient 500** (only the first call fails). Production rule: real outages are far more often brief than permanent, so a team must know how it behaves in both. The manager is built with **no failure-handling code**, so the retry / degrade / lie result is what it truly does. In the report we also *recommend* the production-standard fix (bounded retry with backoff, then an explicit "version check unavailable" answer) but do **not** implement it — Week 8 was the mitigation week, and adding it now would hide the finding.
- **D7 — Environment: existing Python 3.9 venv, no installs.** Production rule: change nothing that isn't being tested. We save the model name, package versions and date with the results so the race can be reproduced.
- **D8 — Worker interface (CHANGED).** Workers run in-process (so both arms share one code path and the only difference measured is the pattern), **but behind an HTTP-shaped contract**: every worker call returns `{status, body}`, and the manager-side code treats `status ≥ 400` exactly as it would over a network. Swapping in a real HTTP/A2A worker later is then a one-line change. Safety rails that are *not* failure handling are allowed: a per-worker timeout (30 s) and the delegation cap. Known limitation we will state: in-process workers hide real network latency, so a deployed squad would be slightly slower than we measure. The A2A bonus is documents only (AgentCard + lifecycle mapping), not a live A2A server.
- **D9 — Keep/kill rule written BEFORE the race (NEW).** Production rule: ship criteria are agreed in advance — that is the strongest defence against sunk-cost bias. **KEEP the squad only if all three hold:** (1) its median-run pass rate beats the single agent by at least 1 question; (2) its cost per question and p50 latency are each ≤ 2× the single agent's; (3) the failure test does not end in "lied". Otherwise **KILL**. Thresholds are saved in `protocol.md` and cannot be edited after the results are in.
- **D10 — Observability (NEW).** Production rule: if you can't trace it you can't debug or bill it. Every question run gets a `trace_id`; every model call is saved as one JSON line (who called, which hand-off, prompt/completion tokens, latency, status). `handoffs.log` and all tables are generated *from* those traces, never typed by hand, and a check asserts the per-hand-off tokens add up to each arm's total.
- **D11 — Same model for every role (kept).** One model (`openai/gpt-oss-120b`), temperature 0. A cheaper model for the workers is the first thing a production team would try *if the squad loses on cost*, but changing the model and the pattern together would make the comparison meaningless; we only mention it as a follow-up idea.

---

*Sources used for the A2A and framework notes: the official A2A specification (task states, AgentCard fields, transport bindings) and 2026 framework comparison articles (CrewAI / AutoGen status).*
