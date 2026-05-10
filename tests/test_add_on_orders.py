from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from bot.config import BotConfig
from bot.data_providers import AlpacaClient, FmpClient, LlmClient
from bot.models import (
    Bar,
    FundamentalsAnnual,
    FundamentalsQuarter,
    OwnershipSnapshot,
    PressRelease,
    SharesOutstandingSnapshot,
    SymbolMetadata,
)
from bot.runner import Bot


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


class FakeAlpaca(AlpacaClient):
    def __init__(
        self,
        bars_by_symbol: Dict[str, List[Bar]],
        metadata_by_symbol: Dict[str, SymbolMetadata],
        positions: List[Tuple[str, int, float, date]],
    ) -> None:
        self.bars_by_symbol = bars_by_symbol
        self.metadata_by_symbol = metadata_by_symbol
        self.positions = positions

    def daily_bars(self, symbol: str, end_date: date, limit: int) -> List[Bar]:
        bars = self.bars_by_symbol.get(symbol, [])
        eligible = [bar for bar in bars if bar.day <= end_date]
        return eligible[-limit:]

    def symbol_metadata(self, symbol: str) -> Optional[SymbolMetadata]:
        return self.metadata_by_symbol.get(symbol)

    def account_equity(self) -> float:
        return 1000.0

    def open_positions(self) -> List[Tuple[str, int, float, date]]:
        return self.positions


class FakeFmp(FmpClient):
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
        raise NotImplementedError("screener unavailable")


class FakeLlm(LlmClient):
    def classify_new(self, text: str) -> bool:
        return True


def test_add_on_order_created_in_uptrend() -> None:
    start = date(2023, 1, 1)
    spy = _market_bars(start, 260)
    qqq = _market_bars(start, 260)
    closes = [100.0 for _ in range(259)] + [103.0]
    aaa = _bars(start, closes)
    alpaca = FakeAlpaca(
        bars_by_symbol={"AAA": aaa, "SPY": spy, "QQQ": qqq},
        metadata_by_symbol={"AAA": SymbolMetadata(symbol="AAA", is_us_common_stock=True)},
        positions=[("AAA", 5, 100.0, start)],
    )
    bot = Bot(alpaca=alpaca, fmp=FakeFmp(), llm=FakeLlm(), config=BotConfig())
    orders, _ = bot.run_daily(["AAA"], aaa[-1].day)
    assert any(order.reason == "can-slim-add" for order in orders)


def test_add_on_skipped_when_stop_loss_hits() -> None:
    start = date(2023, 1, 1)
    spy = _market_bars(start, 260)
    qqq = _market_bars(start, 260)
    closes = [100.0 for _ in range(259)] + [90.0]
    aaa = _bars(start, closes)
    alpaca = FakeAlpaca(
        bars_by_symbol={"AAA": aaa, "SPY": spy, "QQQ": qqq},
        metadata_by_symbol={"AAA": SymbolMetadata(symbol="AAA", is_us_common_stock=True)},
        positions=[("AAA", 5, 100.0, start)],
    )
    bot = Bot(alpaca=alpaca, fmp=FakeFmp(), llm=FakeLlm(), config=BotConfig())
    orders, _ = bot.run_daily(["AAA"], aaa[-1].day)
    assert any(order.reason == "stop-loss" for order in orders)
    assert not any(order.reason == "can-slim-add" for order in orders)


def test_add_on_skipped_when_market_off() -> None:
    start = date(2023, 1, 1)
    spy = _bars(start, [100.0])
    qqq = _bars(start, [100.0])
    closes = [100.0 for _ in range(259)] + [103.0]
    aaa = _bars(start, closes)
    alpaca = FakeAlpaca(
        bars_by_symbol={"AAA": aaa, "SPY": spy, "QQQ": qqq},
        metadata_by_symbol={"AAA": SymbolMetadata(symbol="AAA", is_us_common_stock=True)},
        positions=[("AAA", 5, 100.0, start)],
    )
    bot = Bot(alpaca=alpaca, fmp=FakeFmp(), llm=FakeLlm(), config=BotConfig())
    orders, _ = bot.run_daily(["AAA"], aaa[-1].day)
    assert not any(order.reason == "can-slim-add" for order in orders)
