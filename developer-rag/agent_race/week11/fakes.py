"""Fake stand-ins for openai / chromadb / sentence_transformers, installed
into sys.modules BEFORE agent.py / tools.py / config.py are imported.

WHY THIS FILE EXISTS (see phase0.md for the full environment audit): this
machine has no network access configured for this session and does not have
the `openai`, `chromadb`, or `sentence-transformers` packages installed (the
project was built on a different machine -- see the repo's own commit
history, "week 10 implementation and local files from mac"). Without these
fakes, `import agent` fails immediately at `from openai import OpenAI`,
before a single line of real agent.py logic ever runs.

Installing a fake module object into sys.modules under the real import name
is a standard dependency-injection technique: Python's import system finds
our fake there and never attempts to load the real package, and the REAL
agent.py / tools.py source files run completely unmodified -- only the
external network/ML calls at the very bottom (the actual HTTP request to
Groq, the actual vector index lookup, the actual embedding model) are
replaced with deterministic, scripted behaviour. This is exactly what the
Week 11 brief asks for ("Mock network services in unit tests", "Prefer
deterministic local tests and mocked provider responses").

Every scripted value below is labelled as either REAL (copied verbatim from
an actual historical trace already on disk in this repo --
trajectory_results_baseline.json / trajectory_results_after.json) or
SYNTHETIC (invented for this drill, clearly marked). See
week11/phase3.md and week11/drill.md for exactly which is which and why.
"""

import sys
import types

# ---------------------------------------------------------------------------
# Fake chromadb -- backs tools.py's search_docs / get_openapi_spec /
# check_deprecation with ONE real fact: the assignee -> assignees deprecation
# for GitHub API 2025-06-01's create_issue endpoint. Text and chunk id are
# copied from the REAL golden-set entry, agent_race/golden_sets/gs_medium.json
# case "01" (expected_chunk_id: "github_combined_p5#create_issue_v2025").
# ---------------------------------------------------------------------------

CHUNK_ID = "github_combined_p5#create_issue_v2025"  # REAL -- golden_sets/gs_medium.json, case 01

_SPEC_TEXT = (  # REAL fact content (paraphrased from the golden answer, not the literal PDF text)
    "POST /repos/{owner}/{repo}/issues -- API version 2025-06-01. "
    "assignees (array of string): logins of users to assign to this issue. "
    "Replaces the deprecated single `assignee` string field."
)
_DEPRECATION_TEXT = (  # REAL -- this is golden_sets/gs_medium.json case 01's "answer" field, verbatim
    "The single 'assignee' field is deprecated in favor of the 'assignees' field, "
    "which takes an array of strings (logins)."
)
_PREVIEW_TEXT = (  # REAL -- copied from trajectory_results_baseline.json's actual
    # search_docs result_preview for this exact question
    "Create an issue\nPOST /repos/{owner}/{repo}/issues\nAPI version 2025-06-01\n"
    "Creates an issue on the specified repository . Any user with pull access can create one.\n"
    "Starting in this versio"
)


class _FakeCollection:
    def query(self, query_embeddings, n_results=5, include=None):
        return {
            "ids": [[CHUNK_ID]],
            "documents": [[_PREVIEW_TEXT]],
            "metadatas": [[{
                "source_file": "github_combined_reference.pdf", "page": 5,
                "api_version": "2025-06-01", "endpoint": "create_issue", "has_diff": True,
            }]],
            "distances": [[0.21]],
        }

    def get(self, where=None, include=None):
        clauses = (where or {}).get("$and", [])
        wants_diff = {"has_diff": True} in clauses
        text = _DEPRECATION_TEXT if wants_diff else _SPEC_TEXT
        return {"ids": [CHUNK_ID], "documents": [text]}


class _FakePersistentClient:
    def __init__(self, path=None):
        self.path = path

    def get_collection(self, name=None):
        return _FakeCollection()

    def get_or_create_collection(self, name=None, metadata=None):
        return _FakeCollection()


def install_fake_chromadb():
    mod = types.ModuleType("chromadb")
    mod.PersistentClient = _FakePersistentClient
    sys.modules["chromadb"] = mod


# ---------------------------------------------------------------------------
# Fake sentence_transformers -- tools.py only ever calls
# .encode([text], normalize_embeddings=True)[0].tolist()
# ---------------------------------------------------------------------------

class _FakeVec(list):
    def tolist(self):
        return list(self)


class _FakeSentenceTransformer:
    def __init__(self, name):
        self.name = name

    def encode(self, texts, normalize_embeddings=True, **kwargs):
        return [_FakeVec([0.0] * 8) for _ in texts]


def install_fake_sentence_transformers():
    mod = types.ModuleType("sentence_transformers")
    mod.SentenceTransformer = _FakeSentenceTransformer
    sys.modules["sentence_transformers"] = mod


# ---------------------------------------------------------------------------
# Fake openai -- scripted, deterministic chat.completions.create().
#
# Branches on whether the system prompt passed in is agent.py's
# SYSTEM_PROMPT_MITIGATED (detected by its unique sentence) or the plain
# SYSTEM_PROMPT. Tool-call SEQUENCE and TOKEN COUNTS for both branches are
# copied verbatim from real recorded traces:
#   unmitigated -> trajectory_results_baseline.json, race_id=medium_01
#   mitigated   -> trajectory_results_after.json,   race_id=medium_01
#
# ONLY the unmitigated path's FINAL ANSWER TEXT is synthetic. The real
# baseline run happened to land on the correct fact despite never calling
# the authoritative tool (that is exactly what Week 8 flagged as
# "incomplete_grounding" -- a correct answer today, not a grounded one).
# This fake reproduces the realistic failure that same ungrounded path is
# one unlucky roll away from, so the incident is reproducible on demand
# instead of waiting for it to happen in production again.
# ---------------------------------------------------------------------------

MITIGATION_MARKER = "search_docs returns only a short preview snippet"

SYNTHETIC_BAD_ANSWER = (
    "ANSWER: You can keep using the `assignee` field to set a single user when "
    "creating an issue; GitHub continues to accept it as documented."
)
REAL_GOOD_ANSWER = (  # REAL -- trajectory_results_after.json, race_id=medium_01, "answer" field
    # (en-dashes normalized to ASCII hyphens for console/file-encoding safety)
    "ANSWER: In the 2025-06-01 version of the GitHub API, the deprecated "
    "`assignee` field is replaced by the `assignees` parameter, which accepts "
    "an array of user login strings."
)


class _Msg:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _ToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.function = types.SimpleNamespace(name=name, arguments=arguments)


class _Usage:
    def __init__(self, prompt_tokens, completion_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens


class _Resp:
    def __init__(self, message, usage):
        self.choices = [types.SimpleNamespace(message=message)]
        self.usage = usage


class _FakeCompletions:
    def __init__(self):
        self.call_log = []  # the test inspects this to assert WHICH tools were actually called

    def create(self, model, messages, tools=None, tool_choice=None, temperature=0.0, max_tokens=1024):
        mitigated = any(
            MITIGATION_MARKER in (m.get("content") or "")
            for m in messages if m.get("role") == "system"
        )
        n_prior_tool_msgs = sum(1 for m in messages if m.get("role") == "tool")
        self.call_log.append({"mitigated": mitigated, "n_prior_tool_msgs": n_prior_tool_msgs})

        if not mitigated:
            if n_prior_tool_msgs == 0:
                tc = _ToolCall("c1", "search_docs",
                                '{"query":"2025-06-01 create_issue assignee deprecated single assignee field replaces"}')
                return _Resp(_Msg(None, [tc]), _Usage(703, 126))  # REAL token counts
            return _Resp(_Msg(SYNTHETIC_BAD_ANSWER, None), _Usage(1751, 113))  # REAL token counts

        # mitigated branch -- REAL shape + REAL token counts
        if n_prior_tool_msgs == 0:
            tc = _ToolCall("c1", "check_deprecation",
                            '{"api_version":"2025-06-01","endpoint":"create_issue"}')
            return _Resp(_Msg(None, [tc]), _Usage(807, 127))
        if n_prior_tool_msgs == 1:
            tc = _ToolCall("c2", "get_openapi_spec",
                            '{"api_version":"2025-06-01","endpoint":"create_issue"}')
            return _Resp(_Msg(None, [tc]), _Usage(941, 110))
        return _Resp(_Msg(REAL_GOOD_ANSWER, None), _Usage(1290, 49))


class _FakeChat:
    def __init__(self):
        self.completions = _FakeCompletions()


class _FakeOpenAI:
    def __init__(self, api_key=None, base_url=None, timeout=None, max_retries=None):
        self.chat = _FakeChat()


class _FakeAPIStatusError(Exception):
    def __init__(self, *args, status_code=500, **kwargs):
        super().__init__(*args)
        self.status_code = status_code


def install_fake_openai():
    mod = types.ModuleType("openai")
    mod.OpenAI = _FakeOpenAI
    mod.APIStatusError = _FakeAPIStatusError
    sys.modules["openai"] = mod


def install_fake_dotenv():
    """config.py calls load_dotenv() to read a .env file that does not exist
    on this machine (see phase0.md) -- a no-op stand-in is sufficient since
    we set GROK_API_KEY directly in the test's environment instead."""
    mod = types.ModuleType("dotenv")
    mod.load_dotenv = lambda *a, **kw: False
    sys.modules["dotenv"] = mod


def install_all_fakes():
    install_fake_dotenv()
    install_fake_chromadb()
    install_fake_sentence_transformers()
    install_fake_openai()
