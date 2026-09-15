# Third tool — description diff

**Requirement 1**: a third tool (`check_deprecation`), one job, enum `api_version`
parameter, no overlap with `search_docs` / `get_openapi_spec`.

## v1 draft (rejected — overlaps `get_openapi_spec`)

```json
{
  "name": "check_deprecation",
  "description": "Look up API information for a given version and endpoint, including current parameters and any changes from previous versions.",
  "parameters": {
    "type": "object",
    "properties": {
      "api_version": { "type": "string" },
      "endpoint": { "type": "string" }
    },
    "required": ["api_version", "endpoint"]
  }
}
```

**Why this fails the requirement:**
- "including current parameters" duplicates exactly what `get_openapi_spec` already does — a model reading both descriptions has no signal for which one to call for a plain "what are the parameters" question. This is the exact failure the brief's common-mistakes section names: *"the description bug stays, and it will resurface on the next tool you add."*
- `api_version` is a free-form string, not an enum — the model could call it with `"v2025"`, `"latest"`, `"new"`, or any other paraphrase, none of which match the actual metadata values stored in the index, silently returning nothing.
- The description does two jobs ("look up info" + "any changes"), not one.

## v2 final (shipped, in `tools.py`)

```json
{
  "name": "check_deprecation",
  "description": "Return ONLY what changed, was added, or was deprecated for one specific (api_version, endpoint) pair, relative to its prior version. Does NOT return the current parameter table itself -- call get_openapi_spec for that. Use this when a question asks how something differs between versions, or whether a field is deprecated/replaced.",
  "parameters": {
    "type": "object",
    "properties": {
      "api_version": { "type": "string", "enum": ["2022-11-28", "2025-06-01", "2.x", "3.x"] },
      "endpoint": { "type": "string", "enum": [
        "render_markdown", "create_issue", "create_pull_request", "create_repository",
        "create_release", "cli_general", "java_generator", "python_generator",
        "ignore_file", "config_file"
      ] }
    },
    "required": ["api_version", "endpoint"]
  }
}
```

**What changed and why:**
1. **One job stated, one job denied explicitly** — "Does NOT return the current parameter table" is in the description itself, not left implicit, precisely to stop the model from treating this as a superset of `get_openapi_spec`.
2. **`api_version` is now a closed enum** of the 4 values that actually exist in the index's metadata — no paraphrase can silently miss.
3. **`endpoint` is now a closed enum** of the 10 known endpoints/generators, for the same reason.
4. Enforced at the data layer too, not just the schema: `tools.py`'s `check_deprecation()` returns `{"error": "..."}` if either argument isn't in the known list, rather than a silent empty match.

`get_openapi_spec`'s own description was tightened at the same time, adding the same "Does NOT say what changed... call check_deprecation for that" cross-reference, so the two tools point at each other rather than overlapping.
