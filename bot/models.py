from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Iterable, Optional


class MarketState(str, Enum):
    OFF = "OFF"
    UPTREND = "UPTREND"


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class SymbolMetadata:
    symbol: str
    is_us_common_stock: Optional[bool]
    exchange: Optional[str] = None


@dataclass(frozen=True)
class FundamentalsQuarter:
    report_date: date
    accepted_date: date
    eps: float
    revenue: float
    net_income: Optional[float] = None


@dataclass(frozen=True)
class FundamentalsAnnual:
    report_date: date
    accepted_date: date
    eps: float
    revenue: float
    net_income: Optional[float] = None


@dataclass(frozen=True)
class OwnershipSnapshot:
    report_date: date
    accepted_date: date
    institutional_owners: int


@dataclass(frozen=True)
class SharesOutstandingSnapshot:
    report_date: date
    accepted_date: date
    shares_outstanding: int


@dataclass(frozen=True)
class PressRelease:
    date_published: date
    text: str


@dataclass(frozen=True)
class Candidate:
    symbol: str
    pivot: float
    close: float
    rs_percentile: float


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: int
    entry_price: float
    entry_date: date


@dataclass(frozen=True)
class Order:
    symbol: str
    qty: int
    side: str
    reason: str


def sort_bars(bars: Iterable[Bar]) -> list[Bar]:
    return sorted(bars, key=lambda bar: bar.day)
