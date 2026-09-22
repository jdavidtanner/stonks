from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, List, Optional, Protocol

from bot.models import (
    Bar,
    FundamentalsAnnual,
    FundamentalsQuarter,
    OwnershipSnapshot,
    PressRelease,
    SharesOutstandingSnapshot,
    SymbolMetadata,
)


class AlpacaClient(Protocol):
    def daily_bars(self, symbol: str, end_date: date, limit: int) -> List[Bar]:
        raise NotImplementedError

    def symbol_metadata(self, symbol: str) -> Optional[SymbolMetadata]:
        raise NotImplementedError

    def account_equity(self) -> float:
        raise NotImplementedError

    def open_positions(self) -> List[tuple[str, int, float, date]]:
        raise NotImplementedError


class FmpClient(Protocol):
    def quarterly_fundamentals(self, symbol: str) -> List[FundamentalsQuarter]:
        raise NotImplementedError

    def annual_fundamentals(self, symbol: str) -> List[FundamentalsAnnual]:
        raise NotImplementedError

    def shares_outstanding(self, symbol: str) -> List[SharesOutstandingSnapshot]:
        raise NotImplementedError

    def institutional_ownership(self, symbol: str) -> List[OwnershipSnapshot]:
        raise NotImplementedError

    def press_releases(self, symbol: str, limit: int) -> List[PressRelease]:
        raise NotImplementedError

    def stock_screener(
        self,
        *,
        price_more_than: float,
        volume_more_than: int,
        market_cap_more_than: int,
        limit: int,
    ) -> List[str]:
        raise NotImplementedError


class LlmClient(Protocol):
    def classify_new(self, text: str) -> bool:
        raise NotImplementedError


class VideoLlmClient(LlmClient, Protocol):
    def classify_video(self, video_data: bytes, media_type: str = "video/mp4") -> bool:
        raise NotImplementedError


@dataclass(frozen=True)
class UniverseMember:
    symbol: str

    def __str__(self) -> str:
        return self.symbol
