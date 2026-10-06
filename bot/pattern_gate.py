from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from bot.logger import BotLog
from bot.models import Bar, sort_bars


@dataclass(frozen=True)
class PatternPivot:
    symbol: str
    pivot_price: float
    breakout_day: date
    pattern_type: str


def detect_base_and_pivot(
    symbol: str,
    bars: List[Bar],
    as_of: date,
    log: BotLog,
) -> Optional[PatternPivot]:
    series = [bar for bar in sort_bars(bars) if bar.day <= as_of]
    if len(series) < 76:
        log.info(f"{symbol} rejected: FLAT_BASE_INSUFFICIENT_HISTORY")
        return None

    as_of_idx = len(series) - 1
    base_end_idx = as_of_idx - 1
    if base_end_idx < 0:
        log.info(f"{symbol} rejected: FLAT_BASE_NO_WINDOW")
        return None

    breakout_bar = series[as_of_idx]
    avg50 = sum(bar.volume for bar in series[as_of_idx - 50 : as_of_idx]) / 50

    had_window = False
    any_adjacent_valid_base = False
    any_too_deep = False
    any_breakout_fail = False
    any_volume_fail = False
    any_dry_up_fail = False
    any_tight_fail = False

    for window_len in range(25, 41):
        start_idx = base_end_idx - window_len + 1
        if start_idx < 0:
            continue
        had_window = True
        window = series[start_idx : base_end_idx + 1]
        max_high = max(bar.high for bar in window)
        min_low = min(bar.low for bar in window)
        if max_high <= 0:
            any_too_deep = True
            continue
        depth = (max_high - min_low) / max_high
        if depth > 0.15:
            any_too_deep = True
            continue
        any_adjacent_valid_base = True
        avg_base_vol = sum(bar.volume for bar in window) / len(window)
        if avg_base_vol > 0.90 * avg50:
            any_dry_up_fail = True
            continue
        max_close = max(bar.close for bar in window)
        min_close = min(bar.close for bar in window)
        if max_close <= 0:
            any_tight_fail = True
            continue
        close_range = (max_close - min_close) / max_close
        if close_range > 0.08:
            any_tight_fail = True
            continue
        any_adjacent_valid_base = True
        pivot_price = max_high
        if breakout_bar.close <= pivot_price:
            any_breakout_fail = True
            continue
        if breakout_bar.volume < 1.5 * avg50:
            any_volume_fail = True
            continue
        return PatternPivot(
            symbol=symbol,
            pivot_price=pivot_price,
            breakout_day=as_of,
            pattern_type="flat_base",
        )

    old_base_found = False
    if not any_adjacent_valid_base:
        for end_idx in range(base_end_idx - 1, -1, -1):
            for window_len in range(25, 41):
                start_idx = end_idx - window_len + 1
                if start_idx < 0:
                    continue
                window = series[start_idx : end_idx + 1]
                max_high = max(bar.high for bar in window)
                min_low = min(bar.low for bar in window)
                if max_high <= 0:
                    continue
                depth = (max_high - min_low) / max_high
                if depth > 0.15:
                    continue
                if breakout_bar.close > max_high:
                    old_base_found = True
                    break
            if old_base_found:
                break

    if old_base_found:
        reason = "FLAT_BASE_BASE_NOT_ADJACENT"
    elif any_breakout_fail:
        reason = "FLAT_BASE_NO_BREAKOUT_TODAY"
    elif any_volume_fail:
        reason = "FLAT_BASE_WEAK_BREAKOUT_VOLUME"
    elif any_dry_up_fail:
        reason = "FLAT_BASE_NO_DRY_UP"
    elif any_tight_fail:
        reason = "FLAT_BASE_NOT_TIGHT"
    elif any_too_deep:
        reason = "FLAT_BASE_TOO_DEEP"
    elif had_window:
        reason = "FLAT_BASE_NO_BREAKOUT_TODAY"
    else:
        reason = "FLAT_BASE_NO_WINDOW"

    log.info(f"{symbol} rejected: {reason}")
    return None
