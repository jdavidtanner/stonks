from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, List, Optional, Tuple

from bot.config import BotConfig
from bot.data_providers import AlpacaClient
from bot.logger import BotLog
from bot.models import Bar, SymbolMetadata, sort_bars


@dataclass(frozen=True)
class UniverseResult:
    symbol: str
    bars: List[Bar]


def _avg_dollar_volume(bars: List[Bar], lookback: int = 50) -> Optional[float]:
    if len(bars) < lookback:
        return None
    recent = bars[-lookback:]
    return sum(bar.close * bar.volume for bar in recent) / lookback


def filter_universe(
    symbols: Iterable[str],
    end_date: date,
    alpaca: AlpacaClient,
    config: BotConfig,
    log: BotLog,
) -> List[UniverseResult]:
    results: List[UniverseResult] = []
    for symbol in symbols:
        metadata = alpaca.symbol_metadata(symbol)
        if metadata is None or metadata.is_us_common_stock is not True:
            log.info(f"{symbol} rejected: non-US common stock or unknown metadata.")
            continue
        bars = sort_bars(alpaca.daily_bars(symbol, end_date=end_date, limit=config.min_history_days))
        if len(bars) < config.min_history_days:
            log.info(f"{symbol} rejected: insufficient price history.")
            continue
        last_close = bars[-1].close
        if last_close < config.min_price:
            log.info(f"{symbol} rejected: price below ${config.min_price:.2f}.")
            continue
        avg_dollar_volume = _avg_dollar_volume(bars)
        if avg_dollar_volume is None:
            log.info(f"{symbol} rejected: illiquid (missing volume history).")
            continue
        if avg_dollar_volume < config.min_avg_dollar_volume:
            log.info(f"{symbol} rejected: illiquid (${avg_dollar_volume:,.0f} ADV).")
            continue
        results.append(UniverseResult(symbol=symbol, bars=bars))
    return results
