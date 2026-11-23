"""Configuration: .traderc.toml (cwd → home → defaults) plus environment overrides.

Environment variables (handy for agents and CI):

* ``TRADE_CONFIG`` — explicit path to a config file.
* ``TRADE_DB`` — explicit path to the SQLite database (wins over the config).

Database location when nothing is configured: ``./trades.db`` if it already
exists (backwards compatible), otherwise ``~/.trading-journal/trades.db`` so an
agent gets the same journal no matter which directory it runs in.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from trading_tracker.models import Config

CONFIG_FILENAME = ".traderc.toml"
DEFAULT_HOME_DB = Path("~/.trading-journal/trades.db")


def find_config() -> Path | None:
    """Search for a config file: $TRADE_CONFIG, ./.traderc.toml, ~/.traderc.toml."""
    env = os.environ.get("TRADE_CONFIG")
    if env:
        return Path(env).expanduser()
    for p in (Path.cwd() / CONFIG_FILENAME, Path.home() / CONFIG_FILENAME):
        if p.is_file():
            return p
    return None


def load_config(path: Path | None = None) -> Config:
    """Load config from file (or defaults) and apply environment overrides."""
    if path is None:
        path = find_config()
    data: dict = {}
    if path is not None and path.is_file():
        with open(path, "rb") as f:
            data = tomllib.load(f)
    cfg = Config(**data)
    env_db = os.environ.get("TRADE_DB")
    if env_db:
        cfg.database.path = env_db
    elif "path" not in data.get("database", {}):
        cfg.database.path = "trades.db" if Path("trades.db").is_file() else str(DEFAULT_HOME_DB)
    elif path is not None and not Path(cfg.database.path).expanduser().is_absolute():
        # Relative paths in a config file are relative to that file, not to the cwd.
        cfg.database.path = str((path.parent / cfg.database.path).expanduser())
    return cfg
