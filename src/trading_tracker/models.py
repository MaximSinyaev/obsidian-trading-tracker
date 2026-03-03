"""Pydantic models for trades, positions, closed trades, and config."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Trade(BaseModel):
    id: int | None = None
    ticker: str
    action: Action
    shares: float = Field(gt=0)
    price: float = Field(gt=0)
    commission: float = Field(default=0, ge=0)
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    strategy: str | None = None
    setup: str | None = None
    confidence: int | None = Field(default=None, ge=1, le=5)
    stop_loss: float | None = None
    target_1: float | None = None
    target_2: float | None = None
    entry_plan: str | None = None
    note_path: str | None = None
    source: str = "manual"
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    position_group: str | None = None
    asset_type: str = "stock"
    currency: str = "USD"

    @field_validator("ticker")
    @classmethod
    def ticker_upper(cls, v: str) -> str:
        return v.upper().strip()

    @field_validator("action", mode="before")
    @classmethod
    def action_upper(cls, v: str) -> str:
        return v.upper().strip()


class ClosedTrade(BaseModel):
    id: int | None = None
    ticker: str
    entry_trade_ids: list[int] = Field(default_factory=list)
    exit_trade_ids: list[int] = Field(default_factory=list)
    shares: float = Field(gt=0)
    avg_entry_price: float
    avg_exit_price: float
    entry_avg_cost: float
    total_commission: float = 0
    gross_pnl: float
    net_pnl: float
    pnl_percent: float
    hold_duration_days: float | None = None
    strategy: str | None = None
    what_worked: str | None = None
    what_failed: str | None = None
    lesson: str | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    closed_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class Position(BaseModel):
    ticker: str
    net_shares: float
    avg_cost: float
    total_commission: float = 0
    first_trade: str
    last_trade: str
    trade_count: int

    @property
    def market_value(self) -> float:
        return self.net_shares * self.avg_cost

    @property
    def unrealized_pnl(self) -> float:
        """Unrealized P&L is 0 without current market price — placeholder."""
        return 0.0


class DatabaseConfig(BaseModel):
    path: str = "trades.db"


class DefaultsConfig(BaseModel):
    commission: float = 0.0
    timezone: str = "Asia/Almaty"
    asset_type: str = "stock"
    source: str = "manual"


class ObsidianConfig(BaseModel):
    vault_path: str = ""
    trading_folder: str = "Trading"


class FxConfig(BaseModel):
    currencies: list[str] = Field(default_factory=lambda: ["USD", "EUR", "RUB", "KZT"])
    base_currency: str = "USD"


class Config(BaseModel):
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    obsidian: ObsidianConfig = Field(default_factory=ObsidianConfig)
    fx: FxConfig = Field(default_factory=FxConfig)

    @property
    def db_path(self) -> Path:
        return Path(self.database.path).expanduser()
