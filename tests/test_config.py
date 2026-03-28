"""Tests for config loading and resolution."""

import os
import stat
from unittest.mock import patch

import pytest

from automission.config import (
    AutomissionConfig,
    _KEY_MAP,
    generate_default_config,
    get_oauth_volumes,
    load_config,
    resolve_api_key,
    resolve_auth_method,
    resolve_default,
)


@pytest.fixture
def config_dir(tmp_path):
    return tmp_path / ".automission"


@pytest.fixture
def config_file(config_dir):
    config_dir.mkdir()
    return config_dir / "config.toml"


class TestLoadConfig:
    def test_returns_empty_when_no_file(self, tmp_path):
        cfg = load_config(tmp_path / "nonexistent.toml")
        assert cfg.defaults == {}
        assert cfg.keys == {}

    def test_loads_valid_toml(self, config_file):
        config_file.write_text("""\
[defaults]
agents = 4
backend = "codex"
max_cost = 25.0

[keys]
anthropic = "sk-ant-test"
codex = "sk-test"

[planner]
model = "claude-sonnet-4-6"

[docker]
image = "myimage:v2"
""")
        cfg = load_config(config_file)
        assert cfg.defaults["agents"] == 4
        assert cfg.defaults["backend"] == "codex"
        assert cfg.defaults["max_cost"] == 25.0
        assert cfg.keys["anthropic"] == "sk-ant-test"
        assert cfg.keys["codex"] == "sk-test"
        assert cfg.planner["model"] == "claude-sonnet-4-6"
        assert cfg.docker["image"] == "myimage:v2"

    def test_handles_invalid_toml(self, config_file):
        config_file.write_text("this is not valid [[[toml")
        cfg = load_config(config_file)
        assert cfg.defaults == {}

    def test_handles_partial_config(self, config_file):
        config_file.write_text("[defaults]\nagents = 3\n")
        cfg = load_config(config_file)
        assert cfg.defaults["agents"] == 3
        assert cfg.keys == {}
        assert cfg.planner == {}

    def test_warns_on_permissive_mode(self, config_file, caplog):
        config_file.write_text("[defaults]\nagents = 2\n")
        config_file.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)  # 640
        import logging

        with caplog.at_level(logging.WARNING):
            load_config(config_file)
        assert "readable by others" in caplog.text


class TestAutomissionConfig:
    def test_get_existing_section_key(self):
        cfg = AutomissionConfig(defaults={"agents": 4})
        assert cfg.get("defaults", "agents") == 4

    def test_get_missing_key_returns_fallback(self):
        cfg = AutomissionConfig(defaults={"agents": 4})
        assert cfg.get("defaults", "missing", "fallback") == "fallback"

    def test_get_missing_section_returns_fallback(self):
        cfg = AutomissionConfig()
        assert cfg.get("nonexistent", "key", 42) == 42


class TestResolveApiKey:
    def test_cli_key_wins(self):
        cfg = AutomissionConfig(keys={"anthropic": "from-config"})
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "from-env"}):
            key = resolve_api_key("claude", cli_key="from-cli", config=cfg)
        assert key == "from-cli"

    def test_env_var_wins_over_config(self):
        cfg = AutomissionConfig(keys={"anthropic": "from-config"})
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "from-env"}):
            key = resolve_api_key("claude", config=cfg)
        assert key == "from-env"

    def test_config_used_when_no_env(self):
        cfg = AutomissionConfig(keys={"anthropic": "from-config"})
        with patch.dict(os.environ, {}, clear=True):
            key = resolve_api_key("claude", config=cfg)
        assert key == "from-config"

    def test_returns_none_when_nothing_set(self):
        cfg = AutomissionConfig()
        with patch.dict(os.environ, {}, clear=True):
            key = resolve_api_key("claude", config=cfg)
        assert key is None

    def test_empty_config_value_skipped(self):
        cfg = AutomissionConfig(keys={"anthropic": ""})
        with patch.dict(os.environ, {}, clear=True):
            key = resolve_api_key("claude", config=cfg)
        assert key is None

    def test_codex_backend(self):
        cfg = AutomissionConfig(keys={"codex": "sk-test"})
        with patch.dict(os.environ, {}, clear=True):
            key = resolve_api_key("codex", config=cfg)
        assert key == "sk-test"

    def test_gemini_backend(self):
        cfg = AutomissionConfig(keys={"gemini": "gemini-key"})
        with patch.dict(os.environ, {}, clear=True):
            key = resolve_api_key("gemini", config=cfg)
        assert key == "gemini-key"

    def test_unknown_backend_returns_none(self):
        key = resolve_api_key("unknown")
        assert key is None


class TestResolveDefault:
    def test_cli_value_wins(self):
        cfg = AutomissionConfig(defaults={"agents": 4})
        assert resolve_default("agents", 8, cfg, 2) == 8

    def test_config_wins_over_builtin(self):
        cfg = AutomissionConfig(defaults={"agents": 4})
        assert resolve_default("agents", 2, cfg, 2) == 4

    def test_builtin_default_when_no_config(self):
        cfg = AutomissionConfig()
        assert resolve_default("agents", 2, cfg, 2) == 2

    def test_none_config_uses_builtin(self):
        assert resolve_default("agents", 2, None, 2) == 2


class TestGenerateDefaultConfig:
    def test_creates_file_with_correct_permissions(self, config_dir):
        path = config_dir / "config.toml"
        result = generate_default_config(path)
        assert result == path
        assert path.exists()
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "config.toml"
        generate_default_config(path)
        assert path.exists()

    def test_content_is_valid_toml(self, config_dir):
        import tomllib

        path = config_dir / "config.toml"
        generate_default_config(path)
        with open(path, "rb") as f:
            data = tomllib.load(f)
        assert "defaults" in data
        assert "keys" in data
        assert "planner" in data
        assert "docker" in data

    def test_default_values(self, config_dir):
        import tomllib

        path = config_dir / "config.toml"
        generate_default_config(path)
        with open(path, "rb") as f:
            data = tomllib.load(f)
        assert data["defaults"]["agents"] == 2
        assert data["defaults"]["backend"] == "claude"
        assert data["defaults"]["max_cost"] == 10.0
        assert data["defaults"]["timeout"] == 3600
        assert data["defaults"]["auth"] == "api_key"
        assert data["docker"]["image"] == "ghcr.io/codance-ai/automission:latest"
        assert data["planner"]["backend"] == "claude"
        assert data["planner"]["auth"] == "api_key"


class TestResolveAuthMethod:
    def test_claude_always_api_key(self):
        cfg = AutomissionConfig(defaults={"auth": "oauth"})
        assert resolve_auth_method("claude", cfg) == "api_key"

    def test_defaults_auth_used(self):
        cfg = AutomissionConfig(defaults={"auth": "oauth"})
        assert resolve_auth_method("codex", cfg) == "oauth"

    def test_section_auth_overrides_defaults(self):
        cfg = AutomissionConfig(
            defaults={"auth": "api_key"},
            planner={"auth": "oauth"},
        )
        assert resolve_auth_method("codex", cfg, section="planner") == "oauth"

    def test_defaults_fallback_when_section_missing(self):
        cfg = AutomissionConfig(
            defaults={"auth": "oauth"},
            planner={},
        )
        assert resolve_auth_method("codex", cfg, section="planner") == "oauth"

    def test_no_config_returns_api_key(self):
        assert resolve_auth_method("codex") == "api_key"

    def test_empty_config_returns_api_key(self):
        cfg = AutomissionConfig()
        assert resolve_auth_method("gemini", cfg) == "api_key"


class TestGetOauthVolumes:
    def test_api_key_returns_empty(self):
        assert get_oauth_volumes("codex", "api_key") == []

    def test_claude_oauth_returns_empty(self):
        """Claude has no OAuth token paths."""
        assert get_oauth_volumes("claude", "oauth") == []

    def test_codex_oauth_with_existing_dir(self, tmp_path):
        """Codex OAuth returns volume mount when dir exists."""
        with patch(
            "automission.config._OAUTH_TOKEN_PATHS",
            {"codex": (str(tmp_path), "/root/.codex")},
        ):
            vols = get_oauth_volumes("codex", "oauth")
            assert len(vols) == 1
            assert vols[0] == (str(tmp_path), "/root/.codex", "rw")

    def test_codex_oauth_missing_dir(self, tmp_path):
        """Returns empty when token dir doesn't exist."""
        fake_dir = str(tmp_path / "nonexistent")
        with patch(
            "automission.config._OAUTH_TOKEN_PATHS",
            {"codex": (fake_dir, "/root/.codex")},
        ):
            vols = get_oauth_volumes("codex", "oauth")
            assert vols == []

    def test_gemini_oauth_with_existing_dir(self, tmp_path):
        with patch(
            "automission.config._OAUTH_TOKEN_PATHS",
            {"gemini": (str(tmp_path), "/root/.gemini")},
        ):
            vols = get_oauth_volumes("gemini", "oauth")
            assert len(vols) == 1
            assert vols[0] == (str(tmp_path), "/root/.gemini", "rw")


class TestKeyMap:
    def test_key_map_codex(self):
        assert _KEY_MAP["codex"] == ("CODEX_API_KEY", "codex")

    def test_key_map_gemini(self):
        assert _KEY_MAP["gemini"] == ("GEMINI_API_KEY", "gemini")
