from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, List, Optional, Tuple

from bot.config import BotConfig
from bot.logger import BotLog
from bot.models import Bar, MarketState, sort_bars


@dataclass(frozen=True)
class DistributionStats:
    count: int
    window_days: int


@dataclass(frozen=True)
class MarketGateResult:
    state: MarketState
    distribution: DistributionStats
    ftd_date: Optional[date]
    rally_day_count: Optional[int]


def _distribution_days(bars: List[Bar], window: int) -> int:
    if len(bars) < 2:
        return 0
    recent = bars[-window:]
    count = 0
    for prev, curr in zip(recent, recent[1:]):
        if curr.close < prev.close and curr.volume > prev.volume:
            count += 1
    return count


def _find_recent_low(bars: List[Bar]) -> Optional[int]:
    if not bars:
        return None
    lowest_close = min(bar.close for bar in bars)
    for idx in range(len(bars) - 1, -1, -1):
        if bars[idx].close == lowest_close:
            return idx
    return None


def _rally_attempt_day_count(bars: List[Bar]) -> Optional[int]:
    low_idx = _find_recent_low(bars)
    if low_idx is None or low_idx >= len(bars) - 1:
        return None
    rally_start = low_idx + 1
    if bars[rally_start].close <= bars[low_idx].close:
        return None
    return len(bars) - rally_start


def _ftd_date(bars: List[Bar], config: BotConfig) -> Optional[date]:
    day_count = _rally_attempt_day_count(bars)
    if day_count is None:
        return None
    for offset in range(config.ftd_min_day, config.ftd_max_day + 1):
        idx = len(bars) - offset
        if idx <= 0:
            continue
        prev = bars[idx - 1]
        curr = bars[idx]
        gain = (curr.close - prev.close) / prev.close
        if gain >= config.ftd_min_gain and curr.volume > prev.volume:
            return curr.day
    return None


def evaluate_market_gate(
    spy_bars: Iterable[Bar],
    qqq_bars: Iterable[Bar],
    config: BotConfig,
    log: BotLog,
) -> MarketGateResult:
    spy = sort_bars(spy_bars)
    qqq = sort_bars(qqq_bars)
    if len(spy) < 2 or len(qqq) < 2:
        log.error("Insufficient index data for market gate; market OFF.")
        return MarketGateResult(MarketState.OFF, DistributionStats(0, 0), None, None)

    spy_dist = _distribution_days(spy, config.distribution_window)
    qqq_dist = _distribution_days(qqq, config.distribution_window)
    dist_count = max(spy_dist, qqq_dist)
    if dist_count >= config.distribution_warning:
        log.warn(f"Distribution day count warning: {dist_count}.")
    if dist_count >= config.distribution_off:
        log.warn("Distribution day limit reached; market OFF.")
        return MarketGateResult(
            MarketState.OFF,
            DistributionStats(dist_count, config.distribution_window),
            None,
            None,
        )

    spy_ftd = _ftd_date(spy, config)
    qqq_ftd = _ftd_date(qqq, config)
    ftd = spy_ftd or qqq_ftd
    rally_count = _rally_attempt_day_count(spy) or _rally_attempt_day_count(qqq)
    if ftd is None or rally_count is None:
        log.warn("No valid follow-through day; market OFF.")
        return MarketGateResult(
            MarketState.OFF,
            DistributionStats(dist_count, config.distribution_window),
            None,
            rally_count,
        )

    log.info(f"Market UPTREND confirmed by FTD on {ftd}.")
    return MarketGateResult(
        MarketState.UPTREND,
        DistributionStats(dist_count, config.distribution_window),
        ftd,
        rally_count,
    )
