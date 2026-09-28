# Error Before/After Transcript

**Question:** What changed about the assignee field for creating an issue between GitHub API version 2022-11-28 and 2025-06-01?

Both runs share an identical conversation up to and including the model having already (mistakenly) called `check_deprecation(api_version="2022-11-28", endpoint="create_issue")` -- the wrong direction for this comparison. Only the tool result's error message differs.

**Single-shot at temperature=0 showed no difference** -- documented honestly below, not hidden -- because the user's own question names both versions explicitly, so the model can recover by re-reading the question alone, regardless of the tool message's quality. This test instead runs 8 trials per condition at temperature=0.7 to check for a difference in *reliability* of recovery, which a single deterministic trial can't reveal.

## BEFORE -- original Week 7/8 message (still in `agent_race/tools.py`)

Tool result given to the model:
```json
{
  "found": false,
  "text": null,
  "note": "No recorded change/deprecation for this version+endpoint."
}
```

**Correct recovery: 8/8 trials**

Per-trial outcomes:
```
{'outcome': 'retried_tool', 'retried_correctly': True, 'call': 'check_deprecation({"api_version":"2025-06-01","endpoint":"create_issue"})'}
{'outcome': 'retried_tool', 'retried_correctly': True, 'call': 'check_deprecation({"api_version":"2025-06-01","endpoint":"create_issue"})'}
{'outcome': 'retried_tool', 'retried_correctly': True, 'call': 'check_deprecation({"api_version":"2025-06-01","endpoint":"create_issue"})'}
{'outcome': 'retried_tool', 'retried_correctly': True, 'call': 'check_deprecation({"api_version":"2025-06-01","endpoint":"create_issue"})'}
{'outcome': 'retried_tool', 'retried_correctly': True, 'call': 'check_deprecation({"api_version":"2025-06-01","endpoint":"create_issue"})'}
{'outcome': 'retried_tool', 'retried_correctly': True, 'call': 'check_deprecation({"api_version":"2025-06-01","endpoint":"create_issue"})'}
{'outcome': 'retried_tool', 'retried_correctly': True, 'call': 'check_deprecation({"api_version":"2025-06-01","endpoint":"create_issue"})'}
{'outcome': 'retried_tool', 'retried_correctly': True, 'call': 'check_deprecation({"api_version":"2025-06-01","endpoint":"create_issue"})'}
```

## AFTER -- this week's rewrite (`docs_search_server.py`)

Tool result given to the model:
```json
{
  "found": false,
  "note": "No recorded change for (2022-11-28, create_issue). This usually means either (a) you passed the OLDER of two versions being compared -- try the newer one instead, or (b) nothing changed for this endpoint between versions."
}
```

**Correct recovery: 8/8 trials**

Per-trial outcomes:
```
{'outcome': 'retried_tool', 'retried_correctly': True, 'call': 'check_deprecation({"api_version":"2025-06-01","endpoint":"create_issue"})'}
{'outcome': 'retried_tool', 'retried_correctly': True, 'call': 'check_deprecation({"api_version":"2025-06-01","endpoint":"create_issue"})'}
{'outcome': 'retried_tool', 'retried_correctly': True, 'call': 'check_deprecation({"api_version":"2025-06-01","endpoint":"create_issue"})'}
{'outcome': 'retried_tool', 'retried_correctly': True, 'call': 'check_deprecation({"api_version":"2025-06-01","endpoint":"create_issue"})'}
{'outcome': 'retried_tool', 'retried_correctly': True, 'call': 'check_deprecation({"api_version":"2025-06-01","endpoint":"create_issue"})'}
{'outcome': 'retried_tool', 'retried_correctly': True, 'call': 'check_deprecation({"api_version":"2025-06-01","endpoint":"create_issue"})'}
{'outcome': 'retried_tool', 'retried_correctly': True, 'call': 'check_deprecation({"api_version":"2025-06-01","endpoint":"create_issue"})'}
{'outcome': 'retried_tool', 'retried_correctly': True, 'call': 'check_deprecation({"api_version":"2025-06-01","endpoint":"create_issue"})'}
```

## Honest conclusion

No measurable difference was found in this test -- see the writeup for why, and what would be needed to detect one.
