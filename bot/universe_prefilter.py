from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Protocol

from bot.data_providers import FmpClient
from bot.config import BotConfig
from bot.logger import BotLog

class UniversePrefilter(Protocol):
    def prefilter(self, end_date: date) -> Optional[List[str]]:
        """Return a smaller symbol list or None if not available."""
        raise NotImplementedError


@dataclass
class NoopPrefilter(UniversePrefilter):
    symbols: List[str]

    def prefilter(self, end_date: date) -> Optional[List[str]]:
        return self.symbols


@dataclass
class FmpScreenerPrefilter(UniversePrefilter):
    fmp: FmpClient
    config: BotConfig
    log: BotLog
    min_volume: int = 500_000
    min_market_cap: int = 300_000_000
    limit: int = 1000

    def prefilter(self, end_date: date) -> Optional[List[str]]:
        try:
            symbols = self.fmp.stock_screener(
                price_more_than=self.config.min_price,
                volume_more_than=self.min_volume,
                market_cap_more_than=self.min_market_cap,
                limit=self.limit,
            )
        except (NotImplementedError, Exception):
            self.log.warn("Universe prefilter: unavailable; using input symbols.")
            return None
        if not symbols:
            return []
        return symbols
