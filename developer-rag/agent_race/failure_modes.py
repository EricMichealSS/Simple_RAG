"""The Week 8 agent-failure-mode taxonomy (distinct from Week 5's RAG-content
failure modes -- these are about the PATH the agent takes, not the docs).
Classification is deterministic and priority-ordered so the same run always
lands in the same bucket on re-classification (needed for a stable before/
after regression comparison).

Modes, most severe first:
  budget_exhausted_no_answer -- ran out of a budget before ever answering
  hallucinated_argument      -- called a tool with a (version, endpoint) pair
                                 that doesn't exist in the corpus at all
  no_tool_used               -- answered (or refused) without calling any
                                 tool at all -- the exact "recited from
                                 memory" bug the problem statement names
  incomplete_grounding       -- called some but not all of the required
                                 tools for this question (e.g. skipped
                                 check_deprecation on a comparison question)
  search_thrash_loop         -- called search_docs more than once (reworded
                                 retries instead of moving to a spec tool)
  clean_pass                 -- none of the above; a correct, grounded path
"""

from trajectory_specs import VALID_PAIRS


def classify(spec: dict, status: str, tool_sequence: list, tool_pairs: list) -> str:
    """tool_sequence: list of tool names in call order.
    tool_pairs: list of (tool_name, api_version, endpoint) for calls that
    carry those args (get_openapi_spec / check_deprecation).
    """
    if status == "budget_exceeded":
        return "budget_exhausted_no_answer"

    for _tool, version, endpoint in tool_pairs:
        if (version, endpoint) not in VALID_PAIRS:
            return "hallucinated_argument"

    if len(tool_sequence) == 0:
        return "no_tool_used"

    if not spec["required_tools"].issubset(set(tool_sequence)):
        return "incomplete_grounding"

    if tool_sequence.count("search_docs") > 1:
        return "search_thrash_loop"

    return "clean_pass"


ALL_MODES = [
    "budget_exhausted_no_answer", "hallucinated_argument", "no_tool_used",
    "incomplete_grounding", "search_thrash_loop", "clean_pass",
]
