"""Configuration loading and resolution for automission."""

from __future__ import annotations

import logging
import os
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".automission"
CONFIG_PATH = CONFIG_DIR / "config.toml"

# Mapping: backend name → (env var name, config key)
_KEY_MAP: dict[str, tuple[str, str]] = {
    "claude": ("ANTHROPIC_API_KEY", "anthropic"),
    "codex": ("CODEX_API_KEY", "codex"),
    "gemini": ("GEMINI_API_KEY", "gemini"),
}

# Recommended models per backend (first entry is the default).
RECOMMENDED_MODELS: dict[str, list[str]] = {
    "claude": [
        "claude-sonnet-4-6",
        "claude-opus-4-6",
        "claude-haiku-4-5",
    ],
    "codex": [
        "gpt-5.4",
        "gpt-5.4-pro",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5.2",
    ],
    "gemini": [
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite-preview",
    ],
}


def default_model(backend: str) -> str:
    """Return the default model for a backend."""
    return RECOMMENDED_MODELS.get(backend, [""])[0]


def _build_default_config(
    agent_backend: str = "claude",
    agent_model: str = "",
    planner_backend: str = "claude",
    planner_model: str = "",
) -> str:
    """Build the default config TOML with the correct models for chosen backends."""
    am = agent_model or default_model(agent_backend)
    pm = planner_model or default_model(planner_backend)
    return f"""\
# automission configuration
# Docs: https://github.com/codance-ai/automission

[defaults]
agents = 2
backend = "{agent_backend}"
model = "{am}"
max_cost = 10.0
timeout = 3600
auth = "api_key"              # "api_key" or "oauth"

[keys]
# API keys (or set via environment variables)
# anthropic = "sk-ant-..."     # or ANTHROPIC_API_KEY
# codex = "sk-..."             # or CODEX_API_KEY
# gemini = "..."               # or GEMINI_API_KEY

[planner]
enabled = true
backend = "{planner_backend}"
model = "{pm}"
auth = "api_key"

[verifier]
model = "{am}"

[docker]
image = "ghcr.io/codance-ai/automission:latest"
"""


# Keep a static fallback for code that references DEFAULT_CONFIG_TOML directly.
DEFAULT_CONFIG_TOML = _build_default_config()


@dataclass
class AutomissionConfig:
    """Parsed automission configuration."""

    defaults: dict[str, Any] = field(default_factory=dict)
    keys: dict[str, str] = field(default_factory=dict)
    planner: dict[str, Any] = field(default_factory=dict)
    verifier: dict[str, Any] = field(default_factory=dict)
    docker: dict[str, Any] = field(default_factory=dict)

    def get(self, section: str, key: str, fallback: Any = None) -> Any:
        """Get a config value by section and key."""
        sec = getattr(self, section, None)
        if isinstance(sec, dict):
            return sec.get(key, fallback)
        return fallback


def load_config(path: Path | None = None) -> AutomissionConfig:
    """Load config.toml and return an AutomissionConfig.

    Returns empty config if file does not exist.
    """
    path = path or CONFIG_PATH
    if not path.exists():
        return AutomissionConfig()

    _check_permissions(path)

    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        logger.error("Failed to parse %s: %s", path, e)
        return AutomissionConfig()

    return AutomissionConfig(
        defaults=raw.get("defaults", {}),
        keys=raw.get("keys", {}),
        planner=raw.get("planner", {}),
        verifier=raw.get("verifier", {}),
        docker=raw.get("docker", {}),
    )


def resolve_api_key(
    backend: str,
    cli_key: str | None = None,
    config: AutomissionConfig | None = None,
) -> str | None:
    """Resolve API key using priority: CLI flag > env var > config.

    Returns None if no key is found.
    """
    if cli_key:
        return cli_key

    env_var, config_key = _KEY_MAP.get(backend, ("", ""))

    if env_var:
        env_val = os.environ.get(env_var)
        if env_val:
            return env_val

    if config and config_key:
        cfg_val = config.keys.get(config_key, "")
        if cfg_val:
            return cfg_val

    return None


def resolve_default(
    key: str,
    cli_value: Any,
    config: AutomissionConfig | None = None,
    builtin_default: Any = None,
) -> Any:
    """Resolve a config value: CLI flag > config [defaults] > built-in default.

    Click passes the built-in default when the user doesn't supply a flag,
    so we detect "user didn't pass this flag" by checking if cli_value == builtin_default.
    """
    if cli_value != builtin_default:
        return cli_value

    if config:
        cfg_val = config.defaults.get(key)
        if cfg_val is not None:
            return cfg_val

    return builtin_default


# Mapping: backend → (host_dir, container_dir) for OAuth token mounts
_OAUTH_TOKEN_PATHS: dict[str, tuple[str, str]] = {
    "codex": (str(Path.home() / ".codex"), "/root/.codex"),
    "gemini": (str(Path.home() / ".gemini"), "/root/.gemini"),
}

# Mapping: backend → login command
_OAUTH_LOGIN_CMDS: dict[str, list[str]] = {
    "codex": ["codex", "login"],
    "gemini": ["gemini", "-p", "hello", "--output-format", "json"],
}


def resolve_auth_method(
    backend: str,
    config: AutomissionConfig | None = None,
    section: str = "defaults",
) -> str:
    """Resolve auth method for a given backend.

    Priority: section-specific auth > defaults auth > "api_key".
    Claude always returns "api_key" (no OAuth support).

    Returns "api_key" or "oauth".
    """
    if backend == "claude":
        return "api_key"

    if config:
        # Check section-specific auth first (e.g. planner.auth)
        if section != "defaults":
            sec = getattr(config, section, None)
            if isinstance(sec, dict):
                val = sec.get("auth")
                if val:
                    return val

        # Fall back to defaults.auth
        val = config.defaults.get("auth")
        if val:
            return val

    return "api_key"


def get_oauth_volumes(backend: str, auth_method: str) -> list[tuple[str, str, str]]:
    """Return OAuth token volume mounts for a backend.

    Returns list of (host_path, container_path, mode) tuples.
    mode is "rw" for OAuth tokens (need write access for token refresh).
    Returns empty list if auth is not "oauth" or backend has no OAuth support.
    """
    if auth_method != "oauth":
        return []
    paths = _OAUTH_TOKEN_PATHS.get(backend)
    if not paths:
        return []
    host_dir, container_dir = paths
    if not Path(host_dir).exists():
        return []
    return [(host_dir, container_dir, "rw")]


def generate_default_config(
    path: Path | None = None,
    *,
    agent_backend: str = "claude",
    agent_auth: str = "api_key",
    agent_model: str = "",
    planner_backend: str = "claude",
    planner_auth: str = "api_key",
    planner_model: str = "",
) -> Path:
    """Generate config.toml with secure permissions.

    Returns the path to the created file.
    """
    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    cfg_toml = _build_default_config(
        agent_backend=agent_backend,
        agent_model=agent_model,
        planner_backend=planner_backend,
        planner_model=planner_model,
    )
    data = tomllib.loads(cfg_toml)
    data["defaults"]["auth"] = agent_auth
    data["planner"]["auth"] = planner_auth

    lines = [
        "# automission configuration",
        "# Docs: https://github.com/codance-ai/automission",
        "",
        "[defaults]",
    ]
    for k, v in data["defaults"].items():
        lines.append(f"{k} = {_toml_value(v)}")
    lines += [
        "",
        "[keys]",
        "# API keys (or set via environment variables)",
        '# anthropic = "sk-ant-..."     # or ANTHROPIC_API_KEY',
        '# codex = "sk-..."             # or CODEX_API_KEY',
        '# gemini = "..."               # or GEMINI_API_KEY',
        "",
        "[planner]",
    ]
    for k, v in data["planner"].items():
        lines.append(f"{k} = {_toml_value(v)}")
    lines += [
        "",
        "[verifier]",
    ]
    for k, v in data["verifier"].items():
        lines.append(f"{k} = {_toml_value(v)}")
    lines += [
        "",
        "[docker]",
    ]
    for k, v in data["docker"].items():
        lines.append(f"{k} = {_toml_value(v)}")
    lines.append("")

    path.write_text("\n".join(lines))
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600
    return path


def _toml_value(v: object) -> str:
    """Format a Python value as a TOML literal."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return f'"{v}"'


def _check_permissions(path: Path) -> None:
    """Warn if config file has overly permissive permissions."""
    try:
        mode = path.stat().st_mode
        if mode & (stat.S_IRGRP | stat.S_IROTH):
            logger.warning(
                "%s is readable by others (mode %o). Run: chmod 600 %s",
                path,
                stat.S_IMODE(mode),
                path,
            )
    except OSError as e:
        logger.debug("Could not check permissions on %s: %s", path, e)
