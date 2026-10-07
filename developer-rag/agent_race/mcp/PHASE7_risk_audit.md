# Phase 7 — Supply-Chain Risk Audit

## What this phase did
Wrote `risk_note.md` — exactly 5 lines, answering the 5 required questions about bolting the `package-registry` server into the agent's trust boundary.

## Why it's analyzed as a real third party despite being simulated
The point of this exercise isn't "is this specific file trustworthy" (it's a 100-line script written for this course) — it's "what does adding *any* second server, one we don't fully control, actually expose us to." That question doesn't depend on who wrote the code; it depends on what the server can reach, what it logs, and what a leaked credential for it could do. Reasoning about the simulation as if it were a genuine unknown third party is the honest way to actually practice the skill, rather than skip the exercise because the specific instance is safe.

## The one substantive judgment call
The verdict is **Ship**, but conditionally — narrowed specifically to "this narrow, read-only capability, as currently scoped." That condition matters more than the headline word: the same server, if it later grew a write or execute tool, or started accepting package names that could be used to probe internal-only registries, would need this audit redone from scratch. A risk verdict is a statement about a specific trust boundary at a specific point in time, not a permanent label on the server.

## Outcome
Moving to Phase 8: consolidating every edge case found across all phases, and the final report.
