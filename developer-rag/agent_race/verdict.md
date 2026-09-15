# Verdict

Applying the decision rule (does the path vary by input?) to these 10 questions: no.
Every question — including all 4 cross-version dependency cases and both
negative/no-answer cases — is answered correctly by the identical fixed sequence
(search_docs → get_openapi_spec → check_deprecation → synthesize); check_deprecation's
diff text already contains the prior version's behavior inline, so no case required
branching on what an earlier step found. The numbers agree: workflow beats the agent
on all four measures — pass rate 100% vs 80%, p50 latency 6.6s vs 25.9s, total tokens
10,690 vs 49,477, cost/question $0.00025 vs $0.00094. The agent's two failures were
both negative cases, where it burned its token budget retrying search_docs on
information that doesn't exist in the corpus, instead of concluding "not found" the
way the fixed pipeline's bounded 4 steps naturally do. Verdict: ship the workflow;
none of these 10 questions needs an agent.

(144 words)
