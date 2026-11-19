# Integrated evaluation run — 2026-11-03T19:45:19.564527+00:00

cases: 40 · executed: 40 · status: {'pass': 40} · pass rate over executed cases: 100.0%

## Cases

| id | tier | status | category | modes | origin | title |
|---|---|---|---|---|---|---|
| v01 | offline | pass | version_resolution | version_confusion | week6:version_confusion_w6 | An explicit version beats anything in the question and scopes retrieval |
| v02 | offline | pass | version_resolution | version_confusion | week6:version_confusion_w6 | The version is read from the question when none is passed |
| v03 | offline | pass | version_resolution | version_confusion, false_refusal | golden:gs_easy (questions without a version) | No version, or an unsupported one: ask the caller; nothing is retrieved, no model is called |
| v04 | offline | pass | version_resolution | version_confusion, cross_document | golden:gs_hard (comparison questions) | An explicit comparison is the only way to reach two versions, and both are labelled |
| f01 | offline | pass | strict_filtering | version_confusion, retrieval_miss | week6:mode3_answerable_wrong_chunks | No wrong-version chunk, and the asked endpoint is found, for every (version, endpoint) pair |
| f02 | offline | pass | strict_filtering | version_confusion, unsafe_content | new | A vector store whose filter is broken fails closed instead of answering |
| d01 | offline | pass | deprecation_reporting | version_confusion | new | An older caller gets a LABELLED upgrade note, never newer text presented as its own behaviour |
| g01 | live | pass | version_tagged_citations | false_refusal, retrieval_miss | golden:gs_easy#12 | GitHub 2022-11-28: HTTP status for creating a pull request |
| g02 | live | pass | version_tagged_citations | false_refusal, retrieval_miss | golden:gs_easy#07 | GitHub 2022-11-28: OAuth scopes to create a repository |
| g03 | live | pass | deprecation_reporting | false_refusal, version_confusion | golden:gs_medium#01 | GitHub 2025-06-01: the field that replaces the deprecated assignee |
| g04 | live | pass | breaking_changes | false_refusal, version_confusion | golden:gs_medium#02 | Swagger Codegen 3.x: the flag that generates standalone markdown docs |
| g05 | live | pass | breaking_changes | false_refusal, version_confusion | golden:gs_medium#07 | GitHub 2025-06-01: visibility values for creating a repository |
| g06 | live | pass | breaking_changes | false_refusal, version_confusion | golden:gs_medium#09 | GitHub 2025-06-01: the parameter that links a discussion to a release |
| g07 | live | pass | multi_hop | cross_document, false_refusal | golden:gs_hard#06 | Multi-version: make_latest in 2025-06-01 versus the 2022-11-28 heuristic |
| g08 | live | pass | multi_hop | cross_document, false_refusal | golden:gs_hard#11 | Multi-version: how setting the issue type on creation changed |
| g09 | live | pass | abstention | false_refusal, unsupported_answer | golden:gs_easy#13 | Unanswerable (not in the corpus): GraphQL rate limit in 2025-06-01 |
| c01 | offline | pass | version_tagged_citations | unsupported_answer, version_confusion | new | A fabricated citation, or a claim about another version, is rejected and the answer withheld |
| a02 | offline | pass | abstention | generation_unavailable | week6:mode1_groq_error | No answer model available: the user still gets cited, version-correct excerpts |
| o01 | offline | pass | community_obsolete | version_confusion, code_wrong_version | new | An obsolete symbol in a community-style page must not hide the official deprecation warning |
| i01 | offline | pass | injection | unsafe_content | new | A poisoned official page and an obedient model: the exfiltration channel is closed; hidden characters are stripped and flagged |
| i02 | offline | pass | injection | unsafe_content | new | A poisoned UPLOAD and an obedient model: flagged at upload, kept as data, exfiltration closed |
| u01 | live | pass | upload_qa | false_refusal, isolation_breach | new | Question about the user's own upload: answered from it, cited by filename, never mixed with the official corpus |
| x01 | offline | pass | cross_user_isolation | isolation_breach | new | Cross-user isolation: another owner sees nothing of it; the same owner sees only uploads; official questions never touch uploads |
| x02 | offline | pass | cross_user_isolation | isolation_breach | new | Deleting an upload invalidates it: no retrieval afterwards, and the delete is physical |
| x03 | offline | pass | cache_isolation | version_confusion, isolation_breach | new | One engine instance, the same question for two versions: no state carries over |
| m01 | offline | pass | mcp | tool_path_failure | new | MCP: tools are discovered at runtime and allow-listed; the calls the graph chose carry the caller's version |
| m02 | offline | pass | mcp | tool_path_failure | new | MCP failure: every tool call times out; the request is still served, from the in-process path, and the trace says so |
| m03 | offline | pass | mcp | tool_path_failure, code_wrong_version | new | check_symbol is called through the MCP tool for an identifier the passages do not show, with the version enforced |
| k01 | offline | pass | code_validation | code_wrong_version | new | Wrong-version samples are blocked, with a plain explanation: a 2025 field, a field the next version says did not exist, a CLI flag 3.x added |
| k02 | offline | pass | code_validation | over_blocking | observed:phase6 (X-GitHub-Api-Version calibration) | A documented header the passage does not show is accepted, and the footer says what was and was not checked |
| k03 | offline | pass | code_validation | code_wrong_version | new | A Python sample that does not parse is never delivered |
| k04 | live | pass | code_validation | code_wrong_version, false_refusal | new | Live: a code sample from the real model parses, and no identifier belongs only to another version |
| t01 | offline | pass | trace_pii | tool_path_failure | new | One trace id spans the engine and the MCP server process, with the server's spans nested under the client call |
| t02 | offline | pass | trace_pii | unsafe_content | new | A question containing an email and a token: redacted before persistence, counted, never stored |
| r01 | offline | pass | tool_routing | tool_path_failure | new | Routing: intent, changelog and specialist follow from the question |
| reg01 | offline | pass | code_validation | over_blocking | observed:phase6 live run (a correct release example was withheld) | REGRESSION: a semver tag in a code sample ("v2.0.0") is not a claim about another version |
| reg02 | offline | pass | code_validation | over_blocking, unsafe_content | observed:phase7 live demo (a correct curl answer was withheld) | REGRESSION: the user's own pasted token echoed in a Bearer header is not an invented secret |
| reg03 | offline | pass | code_validation | over_blocking | observed:phase6 live run (footer listed JSON keys as commands) | REGRESSION: a multi-line quoted JSON body in curl is one argument, not several shell commands |
| reg05 | offline | pass | version_resolution | version_confusion, over_blocking | observed:phase8 live run (the model writes 2022‑11‑28; the version-claim check could not see it) | REGRESSION: a date written with a non-breaking hyphen is the same version, in the question and in an answer's claims |
| reg04 | offline | pass | injection | unsafe_content, generation_unavailable | observed:phase5 live run (degraded mode printed retrieved text unchecked) | REGRESSION: with no answer model, a poisoned page's auto-loading image is not printed as an 'excerpt' |

## Failed checks

none

## Pass rate by taxonomy mode (denominator = cases executed that carry the mode)

| mode | cases | executed | pass | rate |
|---|---|---|---|---|
| code_wrong_version | 5 | 5 | 5 | 100.0% |
| cross_document | 3 | 3 | 3 | 100.0% |
| false_refusal | 12 | 12 | 12 | 100.0% |
| generation_unavailable | 2 | 2 | 2 | 100.0% |
| isolation_breach | 4 | 4 | 4 | 100.0% |
| over_blocking | 5 | 5 | 5 | 100.0% |
| retrieval_miss | 3 | 3 | 3 | 100.0% |
| tool_path_failure | 5 | 5 | 5 | 100.0% |
| unsafe_content | 6 | 6 | 6 | 100.0% |
| unsupported_answer | 2 | 2 | 2 | 100.0% |
| version_confusion | 15 | 15 | 15 | 100.0% |

## Metrics

* retrieval / version filter: 0 wrong-version chunks of 325 returned (0.0%); endpoint recall 20/20
* wrong-version code samples DELIVERED (live model): 0 of 1 (0.0%); scripted wrong-version samples blocked: 3/3
* code outcomes: 11 answers with code (1 live, 10 scripted); syntax {'parsed': 10, 'error': 1}; tiers {'cited': 10, 'deprecated': 1, 'elsewhere_in_version': 2, 'other_version_only': 1, 'absent_per_docs': 2, 'syntax_error': 1}; delivered with a blocking finding: 0
* citation correctness (live answers): 12 of 12 (100.0%); retrieved in this request AND api_version within the allowed versions AND has source_name and location (deterministic; whether a citation SUPPORTS its claim is not checked here)
* priced API cost of this run: 0.004141 USD over 11 live model calls; 24 scripted calls (unpriced by design)

## Not covered

* Version-specific PAGINATION and removed-method scenarios are not in the indexed corpus (the brief's example); the version-specific changes that ARE in it (assignee -> assignees, make_latest, issue type, visibility, --generate-markdown, the asyncio library option) are covered.
* There is no community content in the corpus; `o01` is a controlled fixture (one planted page), not an observation of real community content.
* The engine has no answer or retrieval cache; `x02`/`x03` check invalidation of uploads and the absence of cross-request state, not a cache.
* The Week 6 suite's 26 cases are tied to Stack A's corpus and are preserved untouched; they were not run (and cannot be asked of this engine).
* Scripted-model cases simulate a model behaviour (an obedient model, a wrong-version sample); they test the engine's defences, not how often a real model misbehaves.
* Live cases are a small sample (see the case count), one run each, temperature 0 on a provider that is not perfectly deterministic.
* Hostile-MCP-server behaviour (poisoned tool descriptions/results) is covered by unit tests (tests/test_security_adversarial.py), not by this suite.
