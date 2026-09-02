from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from bot.config import BotConfig
from bot.data_providers import AlpacaClient, FmpClient, LlmClient
from bot.logger import BotLog
from bot.models import Bar, MarketState, Position, PressRelease, SymbolMetadata
from bot.runner import Bot
from bot.trader import ScanResult, build_add_on_orders


def _bars(start: date, closes: List[float], volume: int = 1_000_000) -> List[Bar]:
    return [
        Bar(
            day=start + timedelta(days=idx),
            open=close,
            high=close + 1,
            low=close - 1,
            close=close,
            volume=volume,
        )
        for idx, close in enumerate(closes)
    ]


@dataclass
class FakeAlpaca(AlpacaClient):
    bars_by_symbol: Dict[str, List[Bar]]
    metadata_by_symbol: Dict[str, SymbolMetadata]
    positions: List[Tuple[str, int, float, date]]
    add_state: Dict[str, Tuple[int, Optional[float]]]

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

    def add_state_snapshot(self, symbols: List[str]) -> Dict[str, Tuple[int, Optional[float]]]:
        return {symbol: self.add_state.get(symbol, (0, None)) for symbol in symbols}

    def record_add(self, symbol: str, add_price: float) -> None:
        count, _last = self.add_state.get(symbol, (0, None))
        self.add_state[symbol] = (count + 1, add_price)


class FakeFmp(FmpClient):
    def quarterly_fundamentals(self, symbol: str):
        return []

    def annual_fundamentals(self, symbol: str):
        return []

    def shares_outstanding(self, symbol: str):
        return []

    def institutional_ownership(self, symbol: str):
        return []

    def press_releases(self, symbol: str, limit: int, as_of: date):
        return []

    def stock_screener(
        self,
        *,
        price_more_than: float,
        volume_more_than: int,
        market_cap_more_than: int,
        limit: int,
    ):
        raise NotImplementedError("screener unavailable")


class FakeLlm(LlmClient):
    def classify_new(self, text: str) -> bool:
        return True


def test_add_on_requires_spacing_and_cap() -> None:
    config = BotConfig()
    scan = ScanResult(candidates=[], market_state=MarketState.UPTREND)
    positions = [Position(symbol="AAA", qty=10, entry_price=100.0, entry_date=date(2024, 1, 1))]
    latest_prices = {"AAA": 103.0}
    orders = build_add_on_orders(
        scan,
        positions,
        latest_prices,
        1000.0,
        config,
        BotLog(),
        {"AAA": (0, None)},
    )
    assert any(order.reason == "can-slim-add" for order in orders)

    orders = build_add_on_orders(
        scan,
        positions,
        {"AAA": 104.0},
        1000.0,
        config,
        BotLog(),
        {"AAA": (1, 103.0)},
    )
    assert not orders

    orders = build_add_on_orders(
        scan,
        positions,
        {"AAA": 106.0},
        1000.0,
        config,
        BotLog(),
        {"AAA": (1, 103.0)},
    )
    assert any(order.reason == "can-slim-add" for order in orders)

    orders = build_add_on_orders(
        scan,
        positions,
        {"AAA": 108.0},
        1000.0,
        config,
        BotLog(),
        {"AAA": (2, 106.0)},
    )
    assert not orders


def test_add_on_blocked_when_sell_signal_present() -> None:
    start = date(2024, 1, 1)
    bars = _bars(start, [104.0 for _ in range(49)] + [103.0])
    market_bars = _bars(start, [100.0 for _ in range(50)])
    alpaca = FakeAlpaca(
        bars_by_symbol={"AAA": bars, "SPY": market_bars, "QQQ": market_bars},
        metadata_by_symbol={"AAA": SymbolMetadata(symbol="AAA", is_us_common_stock=True)},
        positions=[("AAA", 10, 100.0, start)],
        add_state={"AAA": (0, None)},
    )
    bot = Bot(alpaca=alpaca, fmp=FakeFmp(), llm=FakeLlm(), config=BotConfig())
    orders, _ = bot.run_daily(["AAA", "SPY", "QQQ"], bars[-1].day)
    assert any(order.reason == "sma50-violation" for order in orders)
    assert not any(order.reason == "can-slim-add" for order in orders)


def test_add_on_fails_closed_when_missing_last_add_price() -> None:
    config = BotConfig()
    scan = ScanResult(candidates=[], market_state=MarketState.UPTREND)
    positions = [Position(symbol="AAA", qty=10, entry_price=100.0, entry_date=date(2024, 1, 1))]
    orders = build_add_on_orders(
        scan,
        positions,
        {"AAA": 106.0},
        1000.0,
        config,
        BotLog(),
        {"AAA": (1, None)},
    )
    assert not orders
