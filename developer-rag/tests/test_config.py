"""Configuration validation: a bad setting is refused at start-up with a message that NAMES the variable, a plain-http remote LLM endpoint (which
would send the API key in clear text) is refused, every operator knob has an environment variable, and the config can never leak the key."""

import pytest

from sdk_engine.config import ConfigError, EngineConfig, get_api_key

KEY = "gsk_" + "a1B2c3D4e5F6g7H8i9J0k1L2"          # assembled at run time: not a literal secret in the source


def test_a_non_numeric_value_is_refused_and_names_the_variable_and_the_value():
    with pytest.raises(ConfigError) as e:
        EngineConfig.from_env({"SDK_ENGINE_TOOL_TIMEOUT_S": "abc"})
    assert "SDK_ENGINE_TOOL_TIMEOUT_S" in str(e.value) and "abc" in str(e.value)


@pytest.mark.parametrize("var, value", [("SDK_ENGINE_TOP_K", "0"), ("SDK_ENGINE_TOP_K", "999"), ("SDK_ENGINE_TOOL_TIMEOUT_S", "-3"),
                                        ("SDK_ENGINE_LLM_MAX_TOKENS", "5"), ("SDK_ENGINE_MAX_DISTANCE", "9"), ("SDK_ENGINE_MAX_UPLOAD_BYTES", "10")])
def test_an_out_of_range_value_is_refused_and_names_the_variable(var, value):
    with pytest.raises(ConfigError) as e:
        EngineConfig.from_env({var: value})
    assert var in str(e.value)


def test_a_collection_name_that_could_carry_a_path_is_refused_and_names_the_variable():
    with pytest.raises(ConfigError) as e:
        EngineConfig.from_env({"SDK_ENGINE_COLLECTION": "../elsewhere"})
    assert "SDK_ENGINE_COLLECTION" in str(e.value)


def test_a_config_error_is_a_value_error_so_existing_handlers_still_catch_it():
    assert issubclass(ConfigError, ValueError)


@pytest.mark.parametrize("url", ["http://evil.example/v1", "http://api.groq.com/openai/v1", "ftp://x/v1", "api.groq.com/openai/v1",
                                 "https://user:pass@api.groq.com/openai/v1"])
def test_a_remote_llm_endpoint_must_be_https_without_credentials(url):
    with pytest.raises(ConfigError) as e:
        EngineConfig.from_env({"SDK_ENGINE_LLM_BASE_URL": url})
    assert "SDK_ENGINE_LLM_BASE_URL" in str(e.value) and "pass" not in str(e.value)       # the message must not echo a credential


@pytest.mark.parametrize("url", ["https://api.groq.com/openai/v1", "http://localhost:8000/v1", "http://127.0.0.1:11434/v1"])
def test_https_remote_and_local_http_endpoints_are_accepted(url):
    assert EngineConfig.from_env({"SDK_ENGINE_LLM_BASE_URL": url}).llm_base_url == url


def test_the_previously_hard_coded_knobs_now_have_environment_variables():
    cfg = EngineConfig.from_env({"SDK_ENGINE_LLM_MAX_TOKENS": "900", "SDK_ENGINE_LLM_TIMEOUT_S": "30", "SDK_ENGINE_TOP_K": "7"})
    assert (cfg.llm_max_tokens, cfg.llm_timeout_s, cfg.top_k) == (900, 30.0, 7)


def test_blank_values_are_ignored_not_treated_as_zero_or_empty():
    cfg = EngineConfig.from_env({"SDK_ENGINE_TOP_K": "  ", "SDK_ENGINE_TOOL_TIMEOUT_S": "", "SDK_ENGINE_COLLECTION": ""})
    assert cfg.top_k == EngineConfig().top_k and cfg.official_collection == "race_docs"


def test_defaults_are_valid_and_direct_construction_still_raises_a_validation_error():
    from pydantic import ValidationError

    EngineConfig()
    with pytest.raises(ValidationError):
        EngineConfig(top_k=0)


def test_the_config_never_holds_or_prints_the_api_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    cfg = EngineConfig.from_env()
    assert KEY not in repr(cfg) and KEY not in cfg.model_dump_json() and KEY not in str(cfg.public_view())
    assert get_api_key() == KEY                            # the key is still available to the one place that needs it


def test_the_public_view_lists_the_effective_settings_for_the_health_check():
    view = EngineConfig.from_env({"SDK_ENGINE_TOP_K": "7"}).public_view()
    assert view["top_k"] == 7 and view["generation_model"] == "openai/gpt-oss-120b" and view["llm_base_url"].startswith("https://")
    assert all(isinstance(v, (str, int, float, bool, list)) for v in view.values())      # JSON-friendly: paths are strings, tuples are lists
    assert "api_key" not in " ".join(view).lower()


# ------------------------------------------------------------------ the documented template cannot drift from the code
def test_env_example_mentions_every_setting_and_loads_as_a_valid_blank_config():
    from dotenv import dotenv_values

    from sdk_engine import config as c
    from sdk_engine.config import ENGINE_ROOT

    names = {v for k, v in vars(c).items() if k.startswith("ENV_") and isinstance(v, str) and k not in ("ENV_DEFAULT_VERSION_PREFIX", "ENV_API_KEY")}
    path = ENGINE_ROOT / ".env.example"
    text = path.read_text(encoding="utf-8")
    assert [n for n in names if n not in text] == [] and c.ENV_DEFAULT_VERSION_PREFIX in text
    values = dotenv_values(path)
    assert set(values) == {"GROQ_API_KEY"} and not values["GROQ_API_KEY"]          # a template: no real value, nothing active but the empty key
    assert EngineConfig.from_env({k: v or "" for k, v in values.items()}) == EngineConfig()
