# Phase 3 — Building the `package-registry` Server (simulated second server)

## What this phase did
Built the second MCP server the assignment names specifically: a package-registry server exposing version/release-date/deprecation lookups. No real, live package-registry MCP server was available to attach to, so this is a small, honestly-labeled simulation — stated plainly in the server's own file header, not disguised. It's still analyzed as a genuine third party in the risk note (Phase 7), because the trust-boundary question ("what can this second server reach, and what happens if its credentials leak") is the same question regardless of who actually wrote its code.

## Files created
- `package_registry_data.json` — a small made-up dataset: 3 packages (`octokit`, `swagger-codegen-cli`, `docs-migration-helper`), each with a couple of versions, some marked deprecated with a note explaining what to upgrade to
- `package_registry_server.py` — the server, 3 tools: `get_package_version`, `get_release_date`, `check_package_deprecated`

## Design choices worth noting
- Package/version names are typed as plain `str`, not `Literal[...]` — deliberately different from `docs_search_server.py`. There, the finite set of API versions/endpoints made an enum the right call. Here, an open-ended "any package name" doesn't have a finite enum to declare, so validation has to happen inside the function body instead of at the schema layer — meaning, unlike Phase 1's discovery, the recoverable-error helpers here (`_package_error`, `_version_error`) really are reachable through the real protocol, not pre-empted by pydantic. Verified directly: querying an unknown package (`left-pad`) returns the full recoverable message, not a generic validation error.
- `check_package_deprecated`'s docstring states outright that it's designed to distinguish "this doesn't exist" from "the registry is down" — directly answering the brief's own warning ("Swallowing the unknown-version case into 'Error: not found', so the model cannot tell 'v4 does not exist yet' from 'the registry is down'").

## Verified standalone
All 3 tools tested directly through a real `ClientSession`: correct latest-version lookup, correct deprecated-version detail (with the upgrade note), correct non-deprecated result for a current version, and the recoverable unknown-package message (which lists what IS known, rather than a bare "not found").

## Outcome
Second server built and verified independently, before touching the agent or its config at all. `mcp_agent.py` has not been opened or modified since the Phase 2 snapshot was taken. Moving to Phase 4: the actual zero-code-change proof.
