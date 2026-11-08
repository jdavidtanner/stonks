from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from bot.data_providers import FmpClient, LlmClient
from bot.logger import BotLog


@dataclass(frozen=True)
class NModuleResult:
    symbol: str
    has_new: bool


def evaluate_n_module(
    symbol: str,
    as_of: date,
    fmp: FmpClient,
    llm: LlmClient,
    log: BotLog,
) -> NModuleResult:
    releases = fmp.press_releases(symbol, limit=5, as_of=as_of)
    eligible = []
    for release in releases:
        published = release.published_date
        # fail closed: an undated or future-dated release is not usable as of as_of
        if published is None or published > as_of:
            log.info(f"{symbol} N module: skipping release ineligible as of {as_of}.")
            continue
        eligible.append(release)
    if not eligible:
        log.info(f"{symbol} N module: no eligible press releases as of {as_of}.")
        return NModuleResult(symbol=symbol, has_new=False)
    has_new = False
    for release in eligible:
        if llm.classify_new(release.text):
            has_new = True
            break
    log.info(f"{symbol} N module classification: {has_new}.")
    return NModuleResult(symbol=symbol, has_new=has_new)
