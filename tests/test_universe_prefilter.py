from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from bot.config import BotConfig
from bot.data_providers import AlpacaClient, FmpClient, LlmClient
from bot.logger import BotLog
from bot.models import (
    Bar,
    FundamentalsAnnual,
    FundamentalsQuarter,
    OwnershipSnapshot,
    PressRelease,
    SharesOutstandingSnapshot,
    SymbolMetadata,
)
from bot.trader import scan_market


def _bars(start: date, closes: List[float], volumes: Optional[List[int]] = None) -> List[Bar]:
    if volumes is None:
        volumes = [1_000_000 for _ in closes]
    bars = []
    for idx, close in enumerate(closes):
        day = start + timedelta(days=idx)
        bars.append(
            Bar(
                day=day,
                open=close * 0.98,
                high=close * 1.02,
                low=close * 0.97,
                close=close,
                volume=volumes[idx],
            )
        )
    return bars


def _market_bars(start: date, total_days: int) -> List[Bar]:
    base_days = total_days - 20
    closes = [100.0 for _ in range(base_days)]
    volumes = [100 for _ in range(base_days)]
    last_close = closes[-1] if closes else 100.0
    last_volume = volumes[-1] if volumes else 100
    for _ in range(20):
        last_close *= 1.015
        last_volume += 10
        closes.append(last_close)
        volumes.append(last_volume)
    return _bars(start, closes, volumes)


@dataclass
class FakeAlpaca(AlpacaClient):
    bars_by_symbol: Dict[str, List[Bar]]
    metadata_by_symbol: Dict[str, SymbolMetadata]
    calls: List[str]

    def daily_bars(self, symbol: str, end_date: date, limit: int) -> List[Bar]:
        self.calls.append(symbol)
        bars = self.bars_by_symbol.get(symbol, [])
        eligible = [bar for bar in bars if bar.day <= end_date]
        return eligible[-limit:]

    def symbol_metadata(self, symbol: str) -> Optional[SymbolMetadata]:
        return self.metadata_by_symbol.get(symbol)

    def account_equity(self) -> float:
        return 1000.0

    def open_positions(self) -> List[Tuple[str, int, float, date]]:
        return []


@dataclass
class FakeFmp(FmpClient):
    screener: List[str]
    raise_screener: bool = False

    def quarterly_fundamentals(self, symbol: str) -> List[FundamentalsQuarter]:
        return []

    def annual_fundamentals(self, symbol: str) -> List[FundamentalsAnnual]:
        return []

    def shares_outstanding(self, symbol: str) -> List[SharesOutstandingSnapshot]:
        return []

    def institutional_ownership(self, symbol: str) -> List[OwnershipSnapshot]:
        return []

    def press_releases(self, symbol: str, limit: int) -> List[PressRelease]:
        return []

    def stock_screener(
        self,
        *,
        price_more_than: float,
        volume_more_than: int,
        market_cap_more_than: int,
        limit: int,
    ) -> List[str]:
        if self.raise_screener:
            raise NotImplementedError("screener unavailable")
        return self.screener[:limit]


class FakeLlm(LlmClient):
    def classify_new(self, text: str) -> bool:
        return True


def test_prefilter_limits_universe_calls() -> None:
    start = date(2023, 1, 1)
    spy = _market_bars(start, 260)
    qqq = _market_bars(start, 260)
    history = _bars(start, [100.0 for _ in range(260)])
    calls: List[str] = []
    alpaca = FakeAlpaca(
        bars_by_symbol={"SPY": spy, "QQQ": qqq, "AAA": history, "BBB": history, "CCC": history},
        metadata_by_symbol={
            "AAA": SymbolMetadata(symbol="AAA", is_us_common_stock=True),
            "BBB": SymbolMetadata(symbol="BBB", is_us_common_stock=True),
            "CCC": SymbolMetadata(symbol="CCC", is_us_common_stock=True),
        },
        calls=calls,
    )
    fmp = FakeFmp(screener=["AAA", "BBB"])
    scan_market(["AAA", "BBB", "CCC"], history[-1].day, alpaca, fmp, FakeLlm(), BotConfig(), BotLog())
    assert "CCC" not in set(calls)


def test_prefilter_falls_back_on_error() -> None:
    start = date(2023, 1, 1)
    spy = _market_bars(start, 260)
    qqq = _market_bars(start, 260)
    history = _bars(start, [100.0 for _ in range(260)])
    calls: List[str] = []
    alpaca = FakeAlpaca(
        bars_by_symbol={"SPY": spy, "QQQ": qqq, "AAA": history, "BBB": history, "CCC": history},
        metadata_by_symbol={
            "AAA": SymbolMetadata(symbol="AAA", is_us_common_stock=True),
            "BBB": SymbolMetadata(symbol="BBB", is_us_common_stock=True),
            "CCC": SymbolMetadata(symbol="CCC", is_us_common_stock=True),
        },
        calls=calls,
    )
    fmp = FakeFmp(screener=[], raise_screener=True)
    scan_market(["AAA", "BBB", "CCC"], history[-1].day, alpaca, fmp, FakeLlm(), BotConfig(), BotLog())
    assert "CCC" in set(calls)
