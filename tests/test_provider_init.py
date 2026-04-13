from btwin_cli.provider_init import (
    _provider_seed_path,
    available_provider_names,
    build_provider_config,
    provider_display_name,
)


def test_codex_is_the_only_available_provider():
    assert available_provider_names() == ["codex"]
    assert provider_display_name("codex") == "Codex"


def test_build_provider_config_returns_codex_models_and_reasoning_levels():
    payload = build_provider_config("codex")

    assert payload["providers"][0]["cli"] == "codex"
    assert payload["providers"][0]["default_model"] == "gpt-5.4"
    assert payload["providers"][0]["default_reasoning_level"] == "medium"
    models = {model["id"]: model for model in payload["providers"][0]["models"]}
    assert "gpt-5.4" in models
    assert models["gpt-5.4"]["reasoning_levels"] == ["none", "low", "medium", "high", "xhigh"]
    assert "gpt-5.4-mini" in models
    assert "gpt-5.3-codex" in models
    assert "gpt-5.3-codex-spark" in models
    assert "gpt-5.2" in models
    assert "conductor" in payload["capabilities"]


def test_provider_seed_file_exists_for_codex():
    assert _provider_seed_path("codex").exists()


def test_build_provider_config_returns_fresh_copy():
    payload = build_provider_config("codex")
    payload["providers"][0]["models"][0]["id"] = "changed"

    fresh = build_provider_config("codex")
    assert fresh["providers"][0]["models"][0]["id"] == "gpt-5.4"
