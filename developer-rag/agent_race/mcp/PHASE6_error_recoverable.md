# Phase 6 — Before/After Recoverable Error Transcript

## What this phase did
Built a controlled test isolating exactly one variable: given an **identical** conversation where the model has already made the wrong-direction `check_deprecation` call (`api_version="2022-11-28"` instead of `"2025-06-01"`, for `create_issue`), does the model's next move depend on *what the tool's error message says* — the original Week 7/8 generic message, vs. this week's rewrite?

## Iterating toward an honest test (worth showing, not hiding)

**Attempt 1** (single-shot, temperature=0, narrow 2-value `api_version` enum): both before and after messages led the model to correctly retry with `2025-06-01`. Discarded as non-discriminating: with only "the version that just failed" and "the only other option" in the enum, retrying correctly is a trivial guess regardless of what the error message says.

**Attempt 2** (single-shot, temperature=0, full 4-value enum matching the real server): still no difference. Root cause: the user's own question explicitly names *both* versions ("between GitHub API version 2022-11-28 **and** 2025-06-01") — the model can recover just by re-reading its own conversation, independent of the tool message's quality.

**Attempt 3** (8 trials per condition, temperature=0.7, to check for a *reliability* gap a single deterministic trial can't reveal): **8/8 correct recovery for BOTH the before and after message.** Full per-trial output saved in `error_before_after.md`.

## Honest conclusion

**No measurable difference was found for this specific test, this specific model, run 16 times total.** This is reported as a real finding, not smoothed over: `openai/gpt-oss-120b` recovers from the wrong-direction call reliably regardless of whether the tool's error message is a bare "not found" or a directional hint — because in this exact scenario, the correct next step is also derivable from the user's own question text, which the richer message doesn't add anything beyond.

**What this does and doesn't mean:**
- It does NOT mean the rewrite was pointless in general — the brief's own worry (the model "cheerfully emits a sample against the deprecated version" after a bare "not found") is about a model *confidently answering wrong* rather than *retrying*. In all 16 trials here, the model chose to retry rather than falsely conclude "nothing changed" — a good sign about this specific model's judgment, but it also means this particular test never got to observe the failure mode the rewrite is meant to prevent, because that failure mode didn't occur even with the weaker message.
- It DOES mean: a test needs a scenario where the correct recovery is genuinely NOT derivable from context the model already has (e.g. a comparison question that doesn't name both versions, forcing the model to depend on the tool telling it what else to try) to actually discriminate message quality. That's a real limitation of this test's design, named honestly rather than papered over with a single cherry-picked anecdote.
- The recoverable message rewrite is kept in `docs_search_server.py` regardless — it's a legitimate improvement in principle (it can only help a weaker model or a harder scenario, never hurt), it just isn't the thing that moved a number in *this* specific measurement.

## Files
- `error_transcript_test.py` — the test script, all 3 attempts documented in its own comments
- `error_before_after.md` — the full transcript deliverable: both messages, all 16 trials, the honest conclusion

## Outcome
Moving to Phase 7: the 5-line supply-chain risk note.
