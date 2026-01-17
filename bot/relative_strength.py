from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from bot.logger import BotLog
from bot.models import Bar, Candidate


def _price_return(bars: List[Bar], lookback: int = 250) -> Optional[float]:
    if len(bars) < lookback:
        return None
    start = bars[-lookback].close
    end = bars[-1].close
    if start <= 0:
        return None
    return (end - start) / start


def compute_rs_percentiles(
    universe: Dict[str, List[Bar]],
    log: BotLog,
) -> Dict[str, float]:
    returns: Dict[str, float] = {}
    for symbol, bars in universe.items():
        perf = _price_return(bars)
        if perf is None:
            log.info(f"{symbol} rejected: insufficient history for RS.")
            continue
        returns[symbol] = perf
    if not returns:
        return {}
    sorted_symbols = sorted(returns.items(), key=lambda item: item[1])
    percentiles: Dict[str, float] = {}
    total = len(sorted_symbols)
    for rank, (symbol, _) in enumerate(sorted_symbols, start=1):
        percentiles[symbol] = 100.0 * rank / total
    return percentiles
