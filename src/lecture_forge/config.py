"""Settings from .env, overridden by the real environment."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

PROVIDERS = {"claude-code", "anthropic", "openai"}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    elevenlabs_api_key: str = field(repr=False)
    voice_id: str
    model_id: str
    style_guide_path: Path
    output_dir: Path
    provider: str
    llm_model: str  # empty: each provider's default model
    anthropic_api_key: str = field(repr=False)
    openai_api_key: str = field(repr=False)

    def require(self, name: str) -> str:
        """Value of a setting a command can't run without, or a message naming its env var."""
        if value := getattr(self, name):
            return value
        raise ConfigError(f"{ENV[name][0]} is not set; add it to .env")


ENV = {  # field: (env var, default)
    "elevenlabs_api_key": ("ELEVENLABS_API_KEY", ""),
    "voice_id": ("ELEVENLABS_VOICE_ID", ""),
    "model_id": ("ELEVENLABS_MODEL_ID", "eleven_v3"),
    "style_guide_path": ("STYLE_GUIDE_PATH", "prompts/style-guide.md"),
    "output_dir": ("LECTURE_FORGE_OUTPUT_DIR", "/mnt/d/Dropbox/Lectures"),
    "provider": ("LECTURE_FORGE_PROVIDER", "claude-code"),
    "llm_model": ("LECTURE_FORGE_LLM_MODEL", ""),
    "anthropic_api_key": ("ANTHROPIC_API_KEY", ""),
    "openai_api_key": ("OPENAI_API_KEY", ""),
}


def load_settings(
    env_file: str | Path | None = None, *, root: str | Path = "."
) -> Settings:
    """Settings for the project at `root`: its .env (unless `env_file` names another), with
    the real environment overriding it. Relative paths in either are under `root`, so the
    result doesn't depend on the folder the program runs from."""
    root = Path(root)
    values = {
        **dotenv_values(root / ".env" if env_file is None else env_file),
        **os.environ,
    }
    raw = {f: values.get(var) or default for f, (var, default) in ENV.items()}
    if raw["provider"] not in PROVIDERS:
        raise ConfigError(
            f"Unknown LECTURE_FORGE_PROVIDER {raw['provider']!r}; use one of {sorted(PROVIDERS)}"
        )
    for f in ("style_guide_path", "output_dir"):
        raw[f] = root / raw[f]  # an absolute path stays as it is
    return Settings(**raw)
