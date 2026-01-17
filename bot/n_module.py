from __future__ import annotations

from dataclasses import dataclass
from typing import List

from bot.data_providers import FmpClient, LlmClient
from bot.logger import BotLog


@dataclass(frozen=True)
class NModuleResult:
    symbol: str
    has_new: bool


def evaluate_n_module(
    symbol: str,
    fmp: FmpClient,
    llm: LlmClient,
    log: BotLog,
) -> NModuleResult:
    releases = fmp.press_releases(symbol, limit=5)
    if not releases:
        log.info(f"{symbol} N module: no press releases available.")
        return NModuleResult(symbol=symbol, has_new=False)
    has_new = False
    for release in releases:
        if llm.classify_new(release):
            has_new = True
            break
    log.info(f"{symbol} N module classification: {has_new}.")
    return NModuleResult(symbol=symbol, has_new=has_new)
