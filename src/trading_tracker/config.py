"""Load configuration from .traderc.toml (cwd → home → defaults)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from trading_tracker.models import Config

CONFIG_FILENAME = ".traderc.toml"


def find_config() -> Path | None:
    """Search for config file in cwd, then home directory."""
    candidates = [
        Path.cwd() / CONFIG_FILENAME,
        Path.home() / CONFIG_FILENAME,
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def load_config(path: Path | None = None) -> Config:
    """Load config from file or return defaults."""
    if path is None:
        path = find_config()
    if path is None or not path.is_file():
        return Config()
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return Config(**data)
