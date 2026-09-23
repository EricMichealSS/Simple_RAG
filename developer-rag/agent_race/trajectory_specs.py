"""Ground truth for trajectory evaluation, written BEFORE running the agent
on any of this -- same discipline as Week 6's judge validation: define what
counts as a correct path first, so classifying real runs against it later
can't be quietly bent to match whatever happened.

Reuses the same 10 questions from Week 7's race (questions.py) -- Week 8
does not add new questions, it adds a second scoring layer on the same
baseline.

For each question:
  required_tools   -- the tool NAMES that must all appear somewhere in the
                       trajectory for the answer to be considered grounded
                       (order between them doesn't matter unless noted)
  allowed_tools    -- the full set of tools that are legitimate to call for
                       this question; anything outside this set is a wrong-
                       tool step
  valid_sequences  -- an explicit SET of accepted exact tool-name sequences
                       (the literal "assert as a set, not one sequence"
                       requirement) -- informational/documentation use;
                       required_tools/allowed_tools drive the actual scoring
  min_steps        -- the fewest tool calls a correct run could use
  expected_pairs   -- the (api_version, endpoint) pair(s) a correct run's
                      get_openapi_spec/check_deprecation calls should use
  is_negative      -- True if the correct answer is a refusal (no real
                      endpoint/fact backs the question)
"""

import json
from pathlib import Path

RACE_DIR = Path(__file__).parent
PAGE_META = json.loads((RACE_DIR / "page_metadata.json").read_text(encoding="utf-8"))

# Every (api_version, endpoint) pair that genuinely exists in the ingested
# docs -- built mechanically from page_metadata.json, not hand-guessed.
VALID_PAIRS = set()
for fname, pages in PAGE_META.items():
    if fname.startswith("_") or fname.startswith("known_"):
        continue
    for page_meta in pages.values():
        VALID_PAIRS.add((page_meta["api_version"], page_meta["endpoint"]))


TRAJECTORY_SPECS = {
    "easy_01": {  # render markdown default mode, version stated in the question
        "required_tools": {"get_openapi_spec"},
        "allowed_tools": {"search_docs", "get_openapi_spec"},
        "valid_sequences": {("get_openapi_spec",), ("search_docs", "get_openapi_spec")},
        "min_steps": 1,
        "expected_pairs": {("2025-06-01", "render_markdown")},
        "is_negative": False,
    },
    "easy_02": {  # swagger v3 maven group id, version stated in the question
        "required_tools": {"get_openapi_spec"},
        "allowed_tools": {"search_docs", "get_openapi_spec"},
        "valid_sequences": {("get_openapi_spec",), ("search_docs", "get_openapi_spec")},
        "min_steps": 1,
        "expected_pairs": {("3.x", "cli_general")},
        "is_negative": False,
    },
    "easy_13": {  # GraphQL rate limit -- no such endpoint exists anywhere in the corpus
        "required_tools": set(),
        "allowed_tools": {"search_docs"},
        "valid_sequences": {("search_docs",)},
        "min_steps": 1,
        "expected_pairs": set(),
        "is_negative": True,
    },
    "medium_01": {  # assignee -> assignees, version+endpoint stated
        "required_tools": {"get_openapi_spec"},
        "allowed_tools": {"search_docs", "get_openapi_spec", "check_deprecation"},
        "valid_sequences": {("get_openapi_spec",), ("search_docs", "get_openapi_spec")},
        "min_steps": 1,
        "expected_pairs": {("2025-06-01", "create_issue")},
        "is_negative": False,
    },
    "medium_07": {  # new visibility enum, version+endpoint stated
        "required_tools": {"get_openapi_spec"},
        "allowed_tools": {"search_docs", "get_openapi_spec", "check_deprecation"},
        "valid_sequences": {("get_openapi_spec",), ("search_docs", "get_openapi_spec")},
        "min_steps": 1,
        "expected_pairs": {("2025-06-01", "create_repository")},
        "is_negative": False,
    },
    "medium_13": {  # fabricated Black-formatter flag -- python_generator IS real, the flag isn't
        "required_tools": set(),
        "allowed_tools": {"search_docs", "get_openapi_spec"},
        "valid_sequences": {("search_docs",), ("get_openapi_spec",)},
        "min_steps": 1,
        "expected_pairs": {("3.x", "python_generator")},
        "is_negative": True,
    },
    "hard_01": {  # DEPENDENCY: context param format, 2022-11-28 vs 2025-06-01
        "required_tools": {"get_openapi_spec", "check_deprecation"},
        "allowed_tools": {"search_docs", "get_openapi_spec", "check_deprecation"},
        "valid_sequences": {
            ("get_openapi_spec", "check_deprecation"), ("check_deprecation", "get_openapi_spec"),
            ("search_docs", "get_openapi_spec", "check_deprecation"),
            ("search_docs", "check_deprecation", "get_openapi_spec"),
        },
        "min_steps": 2,
        "expected_pairs": {("2025-06-01", "render_markdown")},
        "is_negative": False,
    },
    "hard_04": {  # DEPENDENCY: maintainer_can_modify creation-time change
        "required_tools": {"get_openapi_spec", "check_deprecation"},
        "allowed_tools": {"search_docs", "get_openapi_spec", "check_deprecation"},
        "valid_sequences": {
            ("get_openapi_spec", "check_deprecation"), ("check_deprecation", "get_openapi_spec"),
            ("search_docs", "get_openapi_spec", "check_deprecation"),
            ("search_docs", "check_deprecation", "get_openapi_spec"),
        },
        "min_steps": 2,
        "expected_pairs": {("2025-06-01", "create_pull_request")},
        "is_negative": False,
    },
    "hard_06": {  # DEPENDENCY: make_latest replacing the undocumented heuristic
        "required_tools": {"get_openapi_spec", "check_deprecation"},
        "allowed_tools": {"search_docs", "get_openapi_spec", "check_deprecation"},
        "valid_sequences": {
            ("get_openapi_spec", "check_deprecation"), ("check_deprecation", "get_openapi_spec"),
            ("search_docs", "get_openapi_spec", "check_deprecation"),
            ("search_docs", "check_deprecation", "get_openapi_spec"),
        },
        "min_steps": 2,
        "expected_pairs": {("2025-06-01", "create_release")},
        "is_negative": False,
    },
    "hard_11": {  # DEPENDENCY: issue type field, 2022-11-28 vs 2025-06-01
        "required_tools": {"get_openapi_spec", "check_deprecation"},
        "allowed_tools": {"search_docs", "get_openapi_spec", "check_deprecation"},
        "valid_sequences": {
            ("get_openapi_spec", "check_deprecation"), ("check_deprecation", "get_openapi_spec"),
            ("search_docs", "get_openapi_spec", "check_deprecation"),
            ("search_docs", "check_deprecation", "get_openapi_spec"),
        },
        "min_steps": 2,
        "expected_pairs": {("2025-06-01", "create_issue")},
        "is_negative": False,
    },
}
