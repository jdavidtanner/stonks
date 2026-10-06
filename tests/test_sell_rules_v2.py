from __future__ import annotations

from datetime import date, timedelta
from typing import List

from bot.logger import BotLog
from bot.models import Bar, Position
from bot.sell_rules import evaluate_sell_signals


def _bars(start: date, closes: List[float], volumes: List[int], highs: List[float], lows: List[float]) -> List[Bar]:
    return [
        Bar(
            day=start + timedelta(days=idx),
            open=closes[idx],
            high=highs[idx],
            low=lows[idx],
            close=closes[idx],
            volume=volumes[idx],
        )
        for idx in range(len(closes))
    ]


def test_climax_top_triggers_sell() -> None:
    entry_date = date(2024, 1, 1)
    closes = [100.0 for _ in range(20)] + [130.0 for _ in range(10)] + [135.0, 140.0, 138.0]
    highs = [101.0 for _ in closes]
    lows = [99.0 for _ in closes]
    highs[-1] = 150.0
    lows[-1] = 130.0
    volumes = [1_000_000 for _ in closes]
    volumes[-1] = 2_000_000
    bars = _bars(entry_date, closes, volumes, highs, lows)
    sells = evaluate_sell_signals(
        positions=[Position(symbol="AAA", qty=10, entry_price=100.0, entry_date=entry_date)],
        price_history={"AAA": bars},
        as_of=bars[-1].day,
        log=BotLog(),
    )
    assert any(order.reason == "climax-top" for order in sells)


def test_climax_top_does_not_trigger_without_extension() -> None:
    entry_date = date(2024, 1, 1)
    closes = [100.0 for _ in range(20)] + [120.0 for _ in range(10)] + [125.0, 128.0, 126.0]
    highs = [101.0 for _ in closes]
    lows = [99.0 for _ in closes]
    highs[-1] = 150.0
    lows[-1] = 130.0
    volumes = [1_000_000 for _ in closes]
    volumes[-1] = 2_000_000
    bars = _bars(entry_date, closes, volumes, highs, lows)
    sells = evaluate_sell_signals(
        positions=[Position(symbol="AAA", qty=10, entry_price=100.0, entry_date=entry_date)],
        price_history={"AAA": bars},
        as_of=bars[-1].day,
        log=BotLog(),
    )
    assert not any(order.reason == "climax-top" for order in sells)


def test_eight_week_failure_triggers_sell() -> None:
    entry_date = date(2024, 1, 1)
    closes = [100.0 for _ in range(10)] + [120.0 for _ in range(5)]
    closes.extend([118.0, 117.0, 116.0, 115.0, 114.0])
    highs = [close + 1.0 for close in closes]
    lows = [close - 1.0 for close in closes]
    for idx in range(len(closes) - 11, len(closes) - 1):
        lows[idx] = 110.0
    bars = _bars(entry_date, closes, [1_000_000 for _ in closes], highs, lows)
    bars[-1] = Bar(
        day=bars[-1].day,
        open=115.0,
        high=116.0,
        low=100.0,
        close=105.0,
        volume=1_000_000,
    )
    sells = evaluate_sell_signals(
        positions=[Position(symbol="AAA", qty=10, entry_price=100.0, entry_date=entry_date)],
        price_history={"AAA": bars},
        as_of=bars[-1].day,
        log=BotLog(),
    )
    assert any(order.reason == "eight-week-failure" for order in sells)


def test_sell_priority_sma50_over_climax() -> None:
    entry_date = date(2024, 1, 1)
    closes = [100.0 for _ in range(49)] + [90.0]
    highs = [101.0 for _ in closes]
    lows = [99.0 for _ in closes]
    highs[-1] = 150.0
    lows[-1] = 130.0
    volumes = [1_000_000 for _ in closes]
    volumes[-1] = 2_000_000
    bars = _bars(entry_date, closes, volumes, highs, lows)
    sells = evaluate_sell_signals(
        positions=[Position(symbol="AAA", qty=10, entry_price=100.0, entry_date=entry_date)],
        price_history={"AAA": bars},
        as_of=bars[-1].day,
        log=BotLog(),
    )
    reasons = {order.reason for order in sells}
    assert "sma50-violation" in reasons
    assert "climax-top" not in reasons
