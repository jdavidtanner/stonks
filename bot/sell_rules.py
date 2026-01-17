from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional

from bot.logger import BotLog
from bot.models import Bar, Order, Position, sort_bars

POWER_PLAY_GAIN = 0.20
POWER_PLAY_DAYS = 15
HOLD_DISABLE_DAYS = 40


def compute_sma(values: List[float], window: int) -> Optional[float]:
    if len(values) < window or window <= 0:
        return None
    return sum(values[-window:]) / window


def _find_entry_index(bars: List[Bar], entry_date: date) -> Optional[int]:
    for idx, bar in enumerate(bars):
        if bar.day == entry_date:
            return idx
    return None


def _is_power_play(bars: List[Bar], entry_price: float, entry_date: date) -> bool:
    entry_idx = _find_entry_index(bars, entry_date)
    if entry_idx is None:
        return False
    target_price = entry_price * (1 + POWER_PLAY_GAIN)
    end_idx = min(entry_idx + POWER_PLAY_DAYS, len(bars) - 1)
    for idx in range(entry_idx, end_idx + 1):
        if bars[idx].close >= target_price:
            return True
    return False


def _avg_range(bars: List[Bar]) -> Optional[float]:
    if not bars:
        return None
    return sum(bar.high - bar.low for bar in bars) / len(bars)


def detect_climax_top(bars: List[Bar], entry_date: date, entry_price: float) -> bool:
    entry_idx = _find_entry_index(bars, entry_date)
    if entry_idx is None:
        return False
    if len(bars) - entry_idx < 11:
        return False
    if len(bars) < 11:
        return False
    last = bars[-1]
    prior = bars[-2]
    if last.close >= prior.close:
        return False
    avg_range = _avg_range(bars[-11:-1])
    if avg_range is None or avg_range <= 0:
        return False
    last_range = last.high - last.low
    if last_range <= 2.0 * avg_range:
        return False
    avg_volume = compute_sma([float(bar.volume) for bar in bars[-11:-1]], 10)
    if avg_volume is None:
        return False
    if last.volume <= 1.8 * avg_volume:
        return False
    if last.close < entry_price * 1.30:
        return False
    return True


def detect_eight_week_failure(bars: List[Bar], entry_date: date, entry_price: float) -> bool:
    entry_idx = _find_entry_index(bars, entry_date)
    if entry_idx is None:
        return False
    window_end = min(entry_idx + 40, len(bars))
    if window_end - entry_idx < 11:
        return False
    reached_gain = False
    for idx in range(entry_idx, window_end):
        if bars[idx].close >= entry_price * 1.20:
            reached_gain = True
        if not reached_gain:
            continue
        if idx < 10:
            continue
        prior_window = bars[idx - 10 : idx]
        prior_low = min(bar.low for bar in prior_window)
        if bars[idx].close < prior_low:
            return True
    return False


def evaluate_sell_signals(
    positions: List[Position],
    price_history: Dict[str, List[Bar]],
    as_of: date,
    log: BotLog,
) -> List[Order]:
    orders: List[Order] = []
    for position in positions:
        bars = price_history.get(position.symbol)
        if not bars:
            continue
        series = [bar for bar in sort_bars(bars) if bar.day <= as_of]
        if not series:
            continue
        last_close = series[-1].close
        closes = [bar.close for bar in series]
        sma50 = compute_sma(closes, 50)
        if sma50 is not None and last_close < sma50:
            orders.append(
                Order(
                    symbol=position.symbol,
                    qty=position.qty,
                    side="sell",
                    reason="sma50-violation",
                )
            )
            continue
        if detect_climax_top(series, position.entry_date, position.entry_price):
            orders.append(
                Order(
                    symbol=position.symbol,
                    qty=position.qty,
                    side="sell",
                    reason="climax-top",
                )
            )
            continue
        if detect_eight_week_failure(series, position.entry_date, position.entry_price):
            orders.append(
                Order(
                    symbol=position.symbol,
                    qty=position.qty,
                    side="sell",
                    reason="eight-week-failure",
                )
            )
            continue
        pct_gain = (last_close - position.entry_price) / position.entry_price
        if pct_gain >= 0.20:
            power_play = _is_power_play(series, position.entry_price, position.entry_date)
            if power_play:
                entry_idx = _find_entry_index(series, position.entry_date)
                if entry_idx is not None:
                    bars_since_entry = len(series) - entry_idx
                    if bars_since_entry < HOLD_DISABLE_DAYS:
                        continue
            orders.append(
                Order(
                    symbol=position.symbol,
                    qty=position.qty,
                    side="sell",
                    reason="profit-take-20",
                )
            )
    return orders
