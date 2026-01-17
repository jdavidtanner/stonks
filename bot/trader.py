from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, List, Optional, Tuple

from bot.cache import RunCache
from bot.config import BotConfig
from bot.data_providers import AlpacaClient, FmpClient, LlmClient
from bot.fundamentals import evaluate_fundamentals
from bot.logger import BotLog
from bot.market import evaluate_market_gate
from bot.models import Bar, Candidate, MarketState, Order, Position
from bot.n_module import evaluate_n_module
from bot.pattern_gate import detect_base_and_pivot
from bot.relative_strength import compute_rs_percentiles
from bot.supply_demand import evaluate_supply_demand
from bot.universe import filter_universe
from bot.universe_prefilter import FmpScreenerPrefilter, NoopPrefilter


@dataclass(frozen=True)
class ScanResult:
    candidates: List[Candidate]
    market_state: MarketState


def scan_market(
    symbols: Iterable[str],
    end_date: date,
    alpaca: AlpacaClient,
    fmp: FmpClient,
    llm: LlmClient,
    config: BotConfig,
    log: BotLog,
) -> ScanResult:
    cache = RunCache()

    def cached_daily_bars(symbol: str, end_date: date, limit: int) -> List[Bar]:
        return cache.get_or_set(
            ("daily_bars", symbol, end_date, limit),
            lambda: alpaca.daily_bars(symbol, end_date=end_date, limit=limit),
        )

    def cached_symbol_metadata(symbol: str):
        return cache.get_or_set(("symbol_metadata", symbol), lambda: alpaca.symbol_metadata(symbol))

    def cached_quarterly(symbol: str):
        return cache.get_or_set(("quarterly_fundamentals", symbol), lambda: fmp.quarterly_fundamentals(symbol))

    def cached_annual(symbol: str):
        return cache.get_or_set(("annual_fundamentals", symbol), lambda: fmp.annual_fundamentals(symbol))

    def cached_shares(symbol: str):
        return cache.get_or_set(("shares_outstanding", symbol), lambda: fmp.shares_outstanding(symbol))

    def cached_owners(symbol: str):
        return cache.get_or_set(("institutional_ownership", symbol), lambda: fmp.institutional_ownership(symbol))

    def cached_press_releases(symbol: str, limit: int):
        return cache.get_or_set(
            ("press_releases", symbol, limit), lambda: fmp.press_releases(symbol, limit)
        )

    class CachedAlpaca(AlpacaClient):
        def daily_bars(self, symbol: str, end_date: date, limit: int) -> List[Bar]:
            return cached_daily_bars(symbol, end_date, limit)

        def symbol_metadata(self, symbol: str):
            return cached_symbol_metadata(symbol)

        def account_equity(self) -> float:
            return alpaca.account_equity()

        def open_positions(self):
            return alpaca.open_positions()

    class CachedFmp(FmpClient):
        def quarterly_fundamentals(self, symbol: str):
            return cached_quarterly(symbol)

        def annual_fundamentals(self, symbol: str):
            return cached_annual(symbol)

        def shares_outstanding(self, symbol: str):
            return cached_shares(symbol)

        def institutional_ownership(self, symbol: str):
            return cached_owners(symbol)

        def press_releases(self, symbol: str, limit: int):
            return cached_press_releases(symbol, limit)

        def stock_screener(
            self,
            *,
            price_more_than: float,
            volume_more_than: int,
            market_cap_more_than: int,
            limit: int,
        ):
            if not hasattr(fmp, "stock_screener"):
                raise NotImplementedError
            return cache.get_or_set(
                ("stock_screener", price_more_than, volume_more_than, market_cap_more_than, limit),
                lambda: fmp.stock_screener(
                    price_more_than=price_more_than,
                    volume_more_than=volume_more_than,
                    market_cap_more_than=market_cap_more_than,
                    limit=limit,
                ),
            )

    cached_alpaca = CachedAlpaca()
    cached_fmp = CachedFmp()

    prefilter: NoopPrefilter | FmpScreenerPrefilter
    if hasattr(fmp, "stock_screener"):
        prefilter = FmpScreenerPrefilter(
            fmp=cached_fmp,
            config=config,
            log=log,
        )
    else:
        log.warn("Universe prefilter: unavailable; using input symbols.")
        prefilter = NoopPrefilter(list(symbols))
    symbols_to_scan = prefilter.prefilter(end_date)
    if symbols_to_scan is None:
        symbols_to_scan = list(symbols)
    else:
        log.info(f"Universe prefilter: FMP screener returned {len(symbols_to_scan)} symbols.")

    spy_bars = cached_daily_bars("SPY", end_date=end_date, limit=300)
    qqq_bars = cached_daily_bars("QQQ", end_date=end_date, limit=300)
    market_gate = evaluate_market_gate(spy_bars, qqq_bars, config, log)

    if market_gate.state is MarketState.OFF:
        return ScanResult(candidates=[], market_state=MarketState.OFF)

    universe_results = filter_universe(symbols_to_scan, end_date, cached_alpaca, config, log)
    if not universe_results:
        return ScanResult(candidates=[], market_state=market_gate.state)

    rs_percentiles = compute_rs_percentiles(
        {result.symbol: result.bars for result in universe_results}, log
    )
    candidates: List[Candidate] = []
    for result in universe_results:
        percentile = rs_percentiles.get(result.symbol)
        if percentile is None or percentile < config.rs_min_percentile:
            log.info(f"{result.symbol} rejected: RS percentile {percentile}.")
            continue
        fundamentals = evaluate_fundamentals(result.symbol, end_date, cached_fmp, log)
        if fundamentals is None:
            continue
        supply = evaluate_supply_demand(result.symbol, end_date, cached_fmp, log)
        if supply is None:
            continue
        n_result = evaluate_n_module(result.symbol, cached_fmp, llm, log)
        if not n_result.has_new:
            log.info(f"{result.symbol} rejected: N_MODULE_NO_NEW")
            continue
        pattern = detect_base_and_pivot(result.symbol, result.bars, end_date, log)
        if pattern is None:
            continue
        pivot = pattern.pivot_price
        close = result.bars[-1].close
        if close < pivot or close > pivot * (1 + config.max_pivot_buy_pct):
            log.info(f"{result.symbol} rejected: breakout not within 5% pivot buy range")
            continue
        candidates.append(
            Candidate(
                symbol=result.symbol,
                pivot=pivot,
                close=close,
                rs_percentile=percentile,
            )
        )

    candidates.sort(key=lambda candidate: candidate.rs_percentile, reverse=True)
    return ScanResult(candidates=candidates, market_state=market_gate.state)


def _position_dict(positions: Iterable[Position]) -> Dict[str, Position]:
    return {position.symbol: position for position in positions}


def build_orders(
    scan: ScanResult,
    positions: Iterable[Position],
    account_equity: float,
    config: BotConfig,
    log: BotLog,
) -> List[Order]:
    orders: List[Order] = []
    position_map = _position_dict(positions)
    if scan.market_state is MarketState.OFF:
        log.info("Market OFF: no new buys.")
        return orders
    available_slots = max(config.max_positions - len(position_map), 0)
    if available_slots == 0:
        log.info("Max positions reached; no new buys.")
        return orders

    allocation = account_equity / config.max_positions
    for candidate in scan.candidates[:available_slots]:
        if candidate.symbol in position_map:
            log.info(f"{candidate.symbol} skipped: already held.")
            continue
        qty = int(allocation / candidate.close)
        if qty <= 0:
            log.info(f"{candidate.symbol} skipped: allocation too small.")
            continue
        orders.append(
            Order(
                symbol=candidate.symbol,
                qty=qty,
                side="buy",
                reason="can-slim-entry",
            )
        )
    return orders


def check_stops(
    positions: Iterable[Position],
    latest_prices: Dict[str, float],
    config: BotConfig,
    log: BotLog,
) -> List[Order]:
    orders: List[Order] = []
    for position in positions:
        last_price = latest_prices.get(position.symbol)
        if last_price is None:
            log.warn(f"{position.symbol} stop check skipped: missing price.")
            continue
        stop_price = position.entry_price * (1 - config.stop_loss_pct)
        if last_price <= stop_price:
            orders.append(
                Order(
                    symbol=position.symbol,
                    qty=position.qty,
                    side="sell",
                    reason="stop-loss",
                )
            )
    return orders


def check_profit_takes(
    positions: Iterable[Position],
    latest_prices: Dict[str, float],
    as_of: date,
    log: BotLog,
) -> List[Order]:
    orders: List[Order] = []
    for position in positions:
        last_price = latest_prices.get(position.symbol)
        if last_price is None:
            log.warn(f"{position.symbol} profit check skipped: missing price.")
            continue
        gain = (last_price - position.entry_price) / position.entry_price
        holding_days = (as_of - position.entry_date).days
        if gain >= 0.20 and holding_days > 21:
            orders.append(
                Order(
                    symbol=position.symbol,
                    qty=position.qty,
                    side="sell",
                    reason="profit-take",
                )
            )
    return orders


def check_sells(
    positions: Iterable[Position],
    latest_prices: Dict[str, float],
    as_of: date,
    config: BotConfig,
    log: BotLog,
) -> List[Order]:
    orders = []
    orders.extend(check_stops(positions, latest_prices, config, log))
    orders.extend(check_profit_takes(positions, latest_prices, as_of, log))
    return orders


MAX_ADDS_PER_POSITION = 2


def build_add_on_orders(
    scan: ScanResult,
    positions: Iterable[Position],
    latest_prices: Dict[str, float],
    account_equity: float,
    config: BotConfig,
    log: BotLog,
    add_state: Dict[str, Tuple[int, Optional[float]]],
) -> List[Order]:
    if scan.market_state is not MarketState.UPTREND:
        return []
    allocation = account_equity / config.max_positions
    orders: List[Order] = []
    for position in positions:
        last_price = latest_prices.get(position.symbol)
        if last_price is None:
            log.warn(f"{position.symbol} add-on skipped: missing price.")
            continue
        add_count, last_add_price = add_state.get(position.symbol, (0, None))
        if add_count >= MAX_ADDS_PER_POSITION:
            log.info(f"{position.symbol} add-on skipped: add cap reached.")
            continue
        extension_cap = position.entry_price * (1 + config.max_pivot_buy_pct) * 1.10
        if last_price > extension_cap:
            log.info(f"{position.symbol} add-on skipped: price extended.")
            continue
        if add_count > 0 and last_add_price is None:
            log.warn(f"{position.symbol} add-on skipped: missing last add price.")
            continue
        if last_add_price is not None and last_price < last_add_price * 1.02:
            log.info(f"{position.symbol} add-on skipped: spacing not met.")
            continue
        if last_price < position.entry_price * 1.025:
            continue
        qty = int(allocation / last_price)
        if qty <= 0:
            log.info(f"{position.symbol} add-on skipped: allocation too small.")
            continue
        orders.append(
            Order(
                symbol=position.symbol,
                qty=qty,
                side="buy",
                reason="can-slim-add",
            )
        )
    return orders
