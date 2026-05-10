from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional

import pytest

from bot.config import BotConfig
from bot.data_providers import AlpacaClient, FmpClient, LlmClient
from bot.fundamentals import evaluate_fundamentals
from bot.logger import BotLog
from bot.market import evaluate_market_gate
from bot.models import Bar, FundamentalsAnnual, FundamentalsQuarter, OwnershipSnapshot, PressRelease, SharesOutstandingSnapshot, SymbolMetadata, Position
from bot.runner import Bot
from bot.sell_rules import HOLD_DISABLE_DAYS, POWER_PLAY_DAYS, evaluate_sell_signals
from bot.supply_demand import evaluate_supply_demand
from bot.pattern_gate import detect_base_and_pivot
from bot.trader import build_orders, check_stops, scan_market


def _bars(start: date, closes: List[float], volumes: Optional[List[int]] = None) -> List[Bar]:
    if volumes is None:
        volumes = [1_000_000 for _ in closes]
    bars = []
    for idx, close in enumerate(closes):
        day = start + timedelta(days=idx)
        bars.append(
            Bar(
                day=day,
                open=close * 0.98,
                high=close * 1.02,
                low=close * 0.97,
                close=close,
                volume=volumes[idx],
            )
        )
    return bars


@dataclass
class FakeAlpaca(AlpacaClient):
    bars_by_symbol: Dict[str, List[Bar]]
    metadata_by_symbol: Dict[str, SymbolMetadata]
    equity: float = 1000.0
    positions: Optional[List[tuple[str, int, float, date]]] = None

    def daily_bars(self, symbol: str, end_date: date, limit: int) -> List[Bar]:
        bars = self.bars_by_symbol.get(symbol, [])
        return [bar for bar in bars if bar.day <= end_date][-limit:]

    def symbol_metadata(self, symbol: str) -> Optional[SymbolMetadata]:
        return self.metadata_by_symbol.get(symbol)

    def account_equity(self) -> float:
        return self.equity

    def open_positions(self) -> List[tuple[str, int, float, date]]:
        return self.positions or []


@dataclass
class FakeFmp(FmpClient):
    quarterly: Dict[str, List[FundamentalsQuarter]]
    annual: Dict[str, List[FundamentalsAnnual]]
    shares: Dict[str, List[SharesOutstandingSnapshot]]
    owners: Dict[str, List[OwnershipSnapshot]]
    releases: Dict[str, List[PressRelease]]
    screener: List[str] = None
    screener_raises: bool = False

    def quarterly_fundamentals(self, symbol: str) -> List[FundamentalsQuarter]:
        return self.quarterly.get(symbol, [])

    def annual_fundamentals(self, symbol: str) -> List[FundamentalsAnnual]:
        return self.annual.get(symbol, [])

    def shares_outstanding(self, symbol: str) -> List[SharesOutstandingSnapshot]:
        return self.shares.get(symbol, [])

    def institutional_ownership(self, symbol: str) -> List[OwnershipSnapshot]:
        return self.owners.get(symbol, [])

    def press_releases(self, symbol: str, limit: int) -> List[PressRelease]:
        return self.releases.get(symbol, [])[:limit]

    def stock_screener(
        self,
        *,
        price_more_than: float,
        volume_more_than: int,
        market_cap_more_than: int,
        limit: int,
    ) -> List[str]:
        if self.screener is None:
            raise NotImplementedError("screener unavailable")
        if self.screener_raises:
            raise RuntimeError("screener unavailable")
        return (self.screener or [])[:limit]


@dataclass
class FakeLlm(LlmClient):
    result: bool = False

    def classify_new(self, text: str) -> bool:
        return self.result


@dataclass
class CountingAlpaca(FakeAlpaca):
    symbols_requested: List[str] = None

    def daily_bars(self, symbol: str, end_date: date, limit: int) -> List[Bar]:
        if self.symbols_requested is None:
            self.symbols_requested = []
        self.symbols_requested.append(symbol)
        return super().daily_bars(symbol, end_date, limit)

    def symbol_metadata(self, symbol: str) -> Optional[SymbolMetadata]:
        if self.symbols_requested is None:
            self.symbols_requested = []
        self.symbols_requested.append(symbol)
        return super().symbol_metadata(symbol)


def test_market_gate_requires_ftd_and_limits_distribution_days() -> None:
    config = BotConfig()
    base = date(2024, 1, 1)
    closes = [100, 98, 97, 98, 99, 101, 103, 104, 105, 106]
    volumes = [100, 90, 80, 85, 90, 120, 130, 125, 140, 150]
    spy = _bars(base, closes, volumes)
    qqq = _bars(base, closes, volumes)
    log = BotLog()
    result = evaluate_market_gate(spy, qqq, config, log)
    assert result.state == result.state.UPTREND

    heavy_volume = [100, 120, 130, 140, 150, 160, 170, 180, 190, 200]
    down_closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91]
    spy_down = _bars(base, down_closes, heavy_volume)
    qqq_down = _bars(base, down_closes, heavy_volume)
    off_result = evaluate_market_gate(spy_down, qqq_down, config, BotLog())
    assert off_result.state == off_result.state.OFF


def test_fundamentals_reject_future_acceptance_date() -> None:
    symbol = "TEST"
    as_of = date(2024, 6, 1)
    log = BotLog()
    fmp = FakeFmp(
        quarterly={
            symbol: [
                FundamentalsQuarter(report_date=date(2024, 3, 31), accepted_date=date(2025, 4, 1), eps=1.5, revenue=100),
                FundamentalsQuarter(report_date=date(2023, 3, 31), accepted_date=date(2025, 4, 1), eps=1.0, revenue=90),
            ]
        },
        annual={
            symbol: [
                FundamentalsAnnual(report_date=date(2021, 12, 31), accepted_date=date(2025, 2, 1), eps=1.0, revenue=400),
                FundamentalsAnnual(report_date=date(2022, 12, 31), accepted_date=date(2025, 2, 1), eps=1.2, revenue=450),
                FundamentalsAnnual(report_date=date(2023, 12, 31), accepted_date=date(2025, 2, 1), eps=1.4, revenue=480),
            ]
        },
        shares={},
        owners={},
        releases={},
    )
    result = evaluate_fundamentals(symbol, as_of, fmp, log)
    assert result is None


def test_fundamentals_accepts_valid_growth() -> None:
    symbol = "TEST"
    as_of = date(2024, 6, 1)
    log = BotLog()
    fmp = FakeFmp(
        quarterly={
            symbol: [
                FundamentalsQuarter(report_date=date(2024, 3, 31), accepted_date=date(2024, 4, 15), eps=1.4, revenue=110),
                FundamentalsQuarter(report_date=date(2023, 12, 31), accepted_date=date(2024, 1, 15), eps=1.3, revenue=105),
                FundamentalsQuarter(report_date=date(2023, 9, 30), accepted_date=date(2023, 10, 15), eps=1.2, revenue=100),
                FundamentalsQuarter(report_date=date(2023, 3, 31), accepted_date=date(2023, 4, 15), eps=1.0, revenue=90),
                FundamentalsQuarter(report_date=date(2022, 12, 31), accepted_date=date(2023, 1, 15), eps=1.0, revenue=85),
                FundamentalsQuarter(report_date=date(2022, 9, 30), accepted_date=date(2022, 10, 15), eps=1.0, revenue=80),
            ]
        },
        annual={
            symbol: [
                FundamentalsAnnual(report_date=date(2021, 12, 31), accepted_date=date(2022, 2, 1), eps=1.0, revenue=400),
                FundamentalsAnnual(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), eps=1.2, revenue=450),
                FundamentalsAnnual(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), eps=1.4, revenue=480),
            ]
        },
        shares={},
        owners={},
        releases={},
    )
    result = evaluate_fundamentals(symbol, as_of, fmp, log)
    assert result is not None


def test_fundamentals_rejects_net_income_margin_collapse() -> None:
    symbol = "TEST"
    as_of = date(2024, 6, 1)
    log = BotLog()
    fmp = FakeFmp(
        quarterly={
            symbol: [
                FundamentalsQuarter(report_date=date(2024, 3, 31), accepted_date=date(2024, 4, 15), eps=1.4, revenue=120),
                FundamentalsQuarter(report_date=date(2023, 12, 31), accepted_date=date(2024, 1, 15), eps=1.3, revenue=115),
                FundamentalsQuarter(report_date=date(2023, 9, 30), accepted_date=date(2023, 10, 15), eps=1.2, revenue=110),
                FundamentalsQuarter(report_date=date(2023, 3, 31), accepted_date=date(2023, 4, 15), eps=1.0, revenue=100),
                FundamentalsQuarter(report_date=date(2022, 12, 31), accepted_date=date(2023, 1, 15), eps=1.0, revenue=95),
                FundamentalsQuarter(report_date=date(2022, 9, 30), accepted_date=date(2022, 10, 15), eps=1.0, revenue=90),
            ]
        },
        annual={
            symbol: [
                FundamentalsAnnual(report_date=date(2021, 12, 31), accepted_date=date(2022, 2, 1), eps=1.0, revenue=400, net_income=40),
                FundamentalsAnnual(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), eps=1.2, revenue=450, net_income=45),
                FundamentalsAnnual(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), eps=1.4, revenue=500, net_income=10),
            ]
        },
        shares={},
        owners={},
        releases={},
    )
    result = evaluate_fundamentals(symbol, as_of, fmp, log)
    assert result is None
    assert any("annual net income margin collapse" in entry for entry in log.entries)


def test_fundamentals_rejects_eps_spike_with_weak_revenue() -> None:
    symbol = "TEST"
    as_of = date(2024, 6, 1)
    log = BotLog()
    fmp = FakeFmp(
        quarterly={
            symbol: [
                FundamentalsQuarter(report_date=date(2024, 3, 31), accepted_date=date(2024, 4, 1), eps=4.0, revenue=110),
                FundamentalsQuarter(report_date=date(2023, 3, 31), accepted_date=date(2023, 4, 1), eps=1.0, revenue=100),
            ]
        },
        annual={
            symbol: [
                FundamentalsAnnual(report_date=date(2021, 12, 31), accepted_date=date(2022, 2, 1), eps=1.0, revenue=400, net_income=40),
                FundamentalsAnnual(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), eps=1.2, revenue=450, net_income=45),
                FundamentalsAnnual(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), eps=1.4, revenue=480, net_income=48),
            ]
        },
        shares={},
        owners={},
        releases={},
    )
    result = evaluate_fundamentals(symbol, as_of, fmp, log)
    assert result is None
    assert any("EPS spike with weak revenue" in entry for entry in log.entries)


def test_fundamentals_rejects_eps_acceleration() -> None:
    symbol = "TEST"
    as_of = date(2024, 11, 1)
    log = BotLog()
    fmp = FakeFmp(
        quarterly={
            symbol: [
                FundamentalsQuarter(report_date=date(2024, 9, 30), accepted_date=date(2024, 10, 15), eps=1.3, revenue=120),
                FundamentalsQuarter(report_date=date(2024, 6, 30), accepted_date=date(2024, 7, 15), eps=1.4, revenue=115),
                FundamentalsQuarter(report_date=date(2024, 3, 31), accepted_date=date(2024, 4, 15), eps=1.2, revenue=110),
                FundamentalsQuarter(report_date=date(2023, 9, 30), accepted_date=date(2023, 10, 15), eps=1.0, revenue=100),
                FundamentalsQuarter(report_date=date(2023, 6, 30), accepted_date=date(2023, 7, 15), eps=1.0, revenue=95),
                FundamentalsQuarter(report_date=date(2023, 3, 31), accepted_date=date(2023, 4, 15), eps=1.0, revenue=90),
            ]
        },
        annual={
            symbol: [
                FundamentalsAnnual(report_date=date(2021, 12, 31), accepted_date=date(2022, 2, 1), eps=1.0, revenue=400, net_income=40),
                FundamentalsAnnual(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), eps=1.2, revenue=450, net_income=45),
                FundamentalsAnnual(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), eps=1.4, revenue=480, net_income=48),
            ]
        },
        shares={},
        owners={},
        releases={},
    )
    result = evaluate_fundamentals(symbol, as_of, fmp, log)
    assert result is None
    assert any("EPS acceleration failed" in entry for entry in log.entries)


def test_fundamentals_accepts_eps_acceleration() -> None:
    symbol = "TEST"
    as_of = date(2024, 11, 1)
    log = BotLog()
    fmp = FakeFmp(
        quarterly={
            symbol: [
                FundamentalsQuarter(report_date=date(2024, 9, 30), accepted_date=date(2024, 10, 15), eps=1.5, revenue=120),
                FundamentalsQuarter(report_date=date(2024, 6, 30), accepted_date=date(2024, 7, 15), eps=1.4, revenue=115),
                FundamentalsQuarter(report_date=date(2024, 3, 31), accepted_date=date(2024, 4, 15), eps=1.3, revenue=110),
                FundamentalsQuarter(report_date=date(2023, 9, 30), accepted_date=date(2023, 10, 15), eps=1.0, revenue=100),
                FundamentalsQuarter(report_date=date(2023, 6, 30), accepted_date=date(2023, 7, 15), eps=1.0, revenue=95),
                FundamentalsQuarter(report_date=date(2023, 3, 31), accepted_date=date(2023, 4, 15), eps=1.0, revenue=90),
            ]
        },
        annual={
            symbol: [
                FundamentalsAnnual(report_date=date(2021, 12, 31), accepted_date=date(2022, 2, 1), eps=1.0, revenue=400, net_income=40),
                FundamentalsAnnual(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), eps=1.2, revenue=450, net_income=45),
                FundamentalsAnnual(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), eps=1.4, revenue=480, net_income=48),
            ]
        },
        shares={},
        owners={},
        releases={},
    )
    result = evaluate_fundamentals(symbol, as_of, fmp, log)
    assert result is not None


def test_supply_demand_missing_data_fails_closed() -> None:
    symbol = "TEST"
    as_of = date(2024, 6, 1)
    fmp = FakeFmp(
        quarterly={},
        annual={},
        shares={},
        owners={},
        releases={},
    )
    result = evaluate_supply_demand(symbol, as_of, fmp, BotLog())
    assert result is None


def test_supply_demand_rejects_institutional_decline() -> None:
    symbol = "TEST"
    as_of = date(2024, 6, 1)
    log = BotLog()
    fmp = FakeFmp(
        quarterly={},
        annual={},
        shares={
            symbol: [
                SharesOutstandingSnapshot(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), shares_outstanding=50_000_000),
            ]
        },
        owners={
            symbol: [
                OwnershipSnapshot(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), institutional_owners=140),
                OwnershipSnapshot(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), institutional_owners=150),
                OwnershipSnapshot(report_date=date(2024, 12, 31), accepted_date=date(2025, 2, 1), institutional_owners=200),
            ]
        },
        releases={},
    )
    result = evaluate_supply_demand(symbol, as_of, fmp, log)
    assert result is None
    assert any("institutional sponsorship declined" in entry for entry in log.entries)


def test_supply_demand_accepts_non_declining_institutional() -> None:
    symbol = "TEST"
    as_of = date(2024, 6, 1)
    fmp = FakeFmp(
        quarterly={},
        annual={},
        shares={
            symbol: [
                SharesOutstandingSnapshot(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), shares_outstanding=50_000_000),
            ]
        },
        owners={
            symbol: [
                OwnershipSnapshot(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), institutional_owners=180),
                OwnershipSnapshot(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), institutional_owners=150),
            ]
        },
        releases={},
    )
    result = evaluate_supply_demand(symbol, as_of, fmp, BotLog())
    assert result is not None


def _flat_base_series(
    start: date,
    pre_days: int,
    base_days: int,
    pivot: float,
    low: float,
    breakout_close: float,
    breakout_volume: int,
    base_volume: int = 1_000,
    pre_volume: int | None = None,
) -> List[Bar]:
    if pre_volume is None:
        pre_volume = base_volume
    series = []
    for idx in range(pre_days):
        day = start + timedelta(days=idx)
        series.append(
            Bar(
                day=day,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=pre_volume,
            )
        )
    for idx in range(base_days):
        day = start + timedelta(days=pre_days + idx)
        series.append(
            Bar(
                day=day,
                open=pivot - 2,
                high=pivot,
                low=low,
                close=pivot - 1,
                volume=base_volume,
            )
        )
    breakout_day = start + timedelta(days=pre_days + base_days)
    series.append(
        Bar(
            day=breakout_day,
            open=breakout_close - 1,
            high=breakout_close + 1,
            low=breakout_close - 2,
            close=breakout_close,
            volume=breakout_volume,
        )
    )
    return series


def _valid_pattern_inputs() -> tuple[List[Bar], date]:
    start = date(2024, 1, 1)
    series = _flat_base_series(
        start=start,
        pre_days=260,
        base_days=30,
        pivot=105.0,
        low=95.0,
        breakout_close=106.0,
        breakout_volume=1_600_000,
        base_volume=800_000,
        pre_volume=1_200_000,
    )
    return series, series[-1].day


def test_scan_market_requires_pattern_and_pivot_range() -> None:
    config = BotConfig()
    bars, as_of = _valid_pattern_inputs()
    market_bars = _bars(date(2024, 1, 1), [100, 99, 98, 99, 100, 102, 103, 104, 105, 106], [100, 90, 80, 85, 90, 120, 130, 125, 140, 150])
    alpaca = FakeAlpaca(
        bars_by_symbol={"AAA": bars, "SPY": market_bars, "QQQ": market_bars},
        metadata_by_symbol={"AAA": SymbolMetadata(symbol="AAA", is_us_common_stock=True)},
    )
    fmp = FakeFmp(
        quarterly={
            "AAA": [
                FundamentalsQuarter(report_date=date(2024, 9, 30), accepted_date=date(2024, 10, 15), eps=1.5, revenue=120),
                FundamentalsQuarter(report_date=date(2024, 6, 30), accepted_date=date(2024, 7, 15), eps=1.4, revenue=115),
                FundamentalsQuarter(report_date=date(2024, 3, 31), accepted_date=date(2024, 4, 15), eps=1.3, revenue=110),
                FundamentalsQuarter(report_date=date(2023, 9, 30), accepted_date=date(2023, 10, 15), eps=1.0, revenue=100),
                FundamentalsQuarter(report_date=date(2023, 6, 30), accepted_date=date(2023, 7, 15), eps=1.0, revenue=95),
                FundamentalsQuarter(report_date=date(2023, 3, 31), accepted_date=date(2023, 4, 15), eps=1.0, revenue=90),
            ]
        },
        annual={
            "AAA": [
                FundamentalsAnnual(report_date=date(2021, 12, 31), accepted_date=date(2022, 2, 1), eps=1.0, revenue=400),
                FundamentalsAnnual(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), eps=1.2, revenue=450),
                FundamentalsAnnual(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), eps=1.4, revenue=480),
            ]
        },
        shares={
            "AAA": [
                SharesOutstandingSnapshot(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), shares_outstanding=50_000_000)
            ]
        },
        owners={
            "AAA": [
                OwnershipSnapshot(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), institutional_owners=200),
                OwnershipSnapshot(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), institutional_owners=180),
            ]
        },
        releases={"AAA": [PressRelease(date_published=date(2024, 1, 1), text="New product launched.")]},
    )
    scan = scan_market(["AAA"], as_of, alpaca, fmp, FakeLlm(result=True), config, BotLog())
    assert scan.candidates
    bars[-1] = Bar(
        day=bars[-1].day,
        open=112.0,
        high=113.0,
        low=110.0,
        close=112.0,
        volume=1_600_000,
    )
    scan_outside = scan_market(["AAA"], as_of, alpaca, fmp, FakeLlm(result=True), config, BotLog())
    assert not scan_outside.candidates


def test_scan_market_fails_closed_when_pattern_gate_rejects() -> None:
    config = BotConfig()
    bars, as_of = _valid_pattern_inputs()
    bars[-1] = Bar(
        day=bars[-1].day,
        open=105.0,
        high=107.0,
        low=104.0,
        close=106.0,
        volume=1_400_000,
    )
    market_bars = _bars(date(2024, 1, 1), [100, 99, 98, 99, 100, 102, 103, 104, 105, 106], [100, 90, 80, 85, 90, 120, 130, 125, 140, 150])
    alpaca = FakeAlpaca(
        bars_by_symbol={"AAA": bars, "SPY": market_bars, "QQQ": market_bars},
        metadata_by_symbol={"AAA": SymbolMetadata(symbol="AAA", is_us_common_stock=True)},
    )
    fmp = FakeFmp(
        quarterly={
            "AAA": [
                FundamentalsQuarter(report_date=date(2024, 9, 30), accepted_date=date(2024, 10, 15), eps=1.5, revenue=120),
                FundamentalsQuarter(report_date=date(2024, 6, 30), accepted_date=date(2024, 7, 15), eps=1.4, revenue=115),
                FundamentalsQuarter(report_date=date(2024, 3, 31), accepted_date=date(2024, 4, 15), eps=1.3, revenue=110),
                FundamentalsQuarter(report_date=date(2023, 9, 30), accepted_date=date(2023, 10, 15), eps=1.0, revenue=100),
                FundamentalsQuarter(report_date=date(2023, 6, 30), accepted_date=date(2023, 7, 15), eps=1.0, revenue=95),
                FundamentalsQuarter(report_date=date(2023, 3, 31), accepted_date=date(2023, 4, 15), eps=1.0, revenue=90),
            ]
        },
        annual={
            "AAA": [
                FundamentalsAnnual(report_date=date(2021, 12, 31), accepted_date=date(2022, 2, 1), eps=1.0, revenue=400),
                FundamentalsAnnual(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), eps=1.2, revenue=450),
                FundamentalsAnnual(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), eps=1.4, revenue=480),
            ]
        },
        shares={
            "AAA": [
                SharesOutstandingSnapshot(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), shares_outstanding=50_000_000)
            ]
        },
        owners={
            "AAA": [
                OwnershipSnapshot(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), institutional_owners=200),
                OwnershipSnapshot(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), institutional_owners=180),
            ]
        },
        releases={"AAA": [PressRelease(date_published=date(2024, 1, 1), text="New product launched.")]},
    )
    scan = scan_market(["AAA"], as_of, alpaca, fmp, FakeLlm(result=True), config, BotLog())
    assert not scan.candidates


def test_build_orders_respects_position_limit_and_stop_loss() -> None:
    config = BotConfig()
    bars = _bars(date(2023, 1, 1), [20 + idx * 0.1 for idx in range(260)])
    market_bars = _bars(date(2024, 1, 1), [100, 99, 98, 99, 100, 102, 103, 104, 105, 106], [100, 90, 80, 85, 90, 120, 130, 125, 140, 150])
    alpaca = FakeAlpaca(
        bars_by_symbol={"AAA": bars, "SPY": market_bars, "QQQ": market_bars},
        metadata_by_symbol={"AAA": SymbolMetadata(symbol="AAA", is_us_common_stock=True)},
        positions=[("AAA", 10, 100.0, date(2024, 1, 1))],
    )
    fmp = FakeFmp(
        quarterly={
            "AAA": [
                FundamentalsQuarter(report_date=date(2024, 9, 30), accepted_date=date(2024, 10, 15), eps=1.5, revenue=120),
                FundamentalsQuarter(report_date=date(2024, 6, 30), accepted_date=date(2024, 7, 15), eps=1.4, revenue=115),
                FundamentalsQuarter(report_date=date(2024, 3, 31), accepted_date=date(2024, 4, 15), eps=1.3, revenue=110),
                FundamentalsQuarter(report_date=date(2023, 9, 30), accepted_date=date(2023, 10, 15), eps=1.0, revenue=100),
                FundamentalsQuarter(report_date=date(2023, 6, 30), accepted_date=date(2023, 7, 15), eps=1.0, revenue=95),
                FundamentalsQuarter(report_date=date(2023, 3, 31), accepted_date=date(2023, 4, 15), eps=1.0, revenue=90),
            ]
        },
        annual={
            "AAA": [
                FundamentalsAnnual(report_date=date(2021, 12, 31), accepted_date=date(2022, 2, 1), eps=1.0, revenue=400),
                FundamentalsAnnual(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), eps=1.2, revenue=450),
                FundamentalsAnnual(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), eps=1.4, revenue=480),
            ]
        },
        shares={
            "AAA": [
                SharesOutstandingSnapshot(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), shares_outstanding=50_000_000)
            ]
        },
        owners={
            "AAA": [
                OwnershipSnapshot(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), institutional_owners=200),
                OwnershipSnapshot(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), institutional_owners=180),
            ]
        },
        releases={"AAA": [PressRelease(date_published=date(2024, 1, 1), text="New product launched.")]},
    )
    bot = Bot(alpaca=alpaca, fmp=fmp, llm=FakeLlm(result=True), config=config)
    orders, _log = bot.run_daily(["AAA"], date(2024, 6, 1))
    assert not [order for order in orders if order.side == "buy"]
    stop_orders = check_stops(
        positions=[Position(symbol="AAA", qty=10, entry_price=100.0, entry_date=date(2024, 1, 1))],
        latest_prices={"AAA": 90.0},
        config=config,
        log=BotLog(),
    )
    assert [order for order in stop_orders if order.reason == "stop-loss"]


def test_n_module_rejects_without_new() -> None:
    config = BotConfig()
    bars, as_of = _valid_pattern_inputs()
    market_bars = _bars(date(2024, 1, 1), [100, 99, 98, 99, 100, 102, 103, 104, 105, 106], [100, 90, 80, 85, 90, 120, 130, 125, 140, 150])
    alpaca = FakeAlpaca(
        bars_by_symbol={"AAA": bars, "SPY": market_bars, "QQQ": market_bars},
        metadata_by_symbol={"AAA": SymbolMetadata(symbol="AAA", is_us_common_stock=True)},
    )
    fmp = FakeFmp(
        quarterly={
            "AAA": [
                FundamentalsQuarter(report_date=date(2024, 9, 30), accepted_date=date(2024, 10, 15), eps=1.5, revenue=120),
                FundamentalsQuarter(report_date=date(2024, 6, 30), accepted_date=date(2024, 7, 15), eps=1.4, revenue=115),
                FundamentalsQuarter(report_date=date(2024, 3, 31), accepted_date=date(2024, 4, 15), eps=1.3, revenue=110),
                FundamentalsQuarter(report_date=date(2023, 9, 30), accepted_date=date(2023, 10, 15), eps=1.0, revenue=100),
                FundamentalsQuarter(report_date=date(2023, 6, 30), accepted_date=date(2023, 7, 15), eps=1.0, revenue=95),
                FundamentalsQuarter(report_date=date(2023, 3, 31), accepted_date=date(2023, 4, 15), eps=1.0, revenue=90),
            ]
        },
        annual={
            "AAA": [
                FundamentalsAnnual(report_date=date(2021, 12, 31), accepted_date=date(2022, 2, 1), eps=1.0, revenue=400),
                FundamentalsAnnual(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), eps=1.2, revenue=450),
                FundamentalsAnnual(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), eps=1.4, revenue=480),
            ]
        },
        shares={
            "AAA": [
                SharesOutstandingSnapshot(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), shares_outstanding=50_000_000)
            ]
        },
        owners={
            "AAA": [
                OwnershipSnapshot(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), institutional_owners=200),
                OwnershipSnapshot(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), institutional_owners=180),
            ]
        },
        releases={"AAA": [PressRelease(date_published=date(2024, 1, 1), text="New product launched.")]},
    )
    log = BotLog()
    scan = scan_market(["AAA"], as_of, alpaca, fmp, FakeLlm(result=False), config, log)
    assert not scan.candidates
    assert log.entries.count("AAA rejected: N_MODULE_NO_NEW") == 1


def test_n_module_allows_with_new() -> None:
    config = BotConfig()
    bars, as_of = _valid_pattern_inputs()
    market_bars = _bars(
        date(2024, 1, 1),
        [100, 99, 98, 99, 100, 102, 103, 104, 105, 106],
        [100, 90, 80, 85, 90, 120, 130, 125, 140, 150],
    )
    alpaca = FakeAlpaca(
        bars_by_symbol={"AAA": bars, "SPY": market_bars, "QQQ": market_bars},
        metadata_by_symbol={"AAA": SymbolMetadata(symbol="AAA", is_us_common_stock=True)},
    )
    fmp = FakeFmp(
        quarterly={
            "AAA": [
                FundamentalsQuarter(report_date=date(2024, 9, 30), accepted_date=date(2024, 10, 15), eps=1.5, revenue=120),
                FundamentalsQuarter(report_date=date(2024, 6, 30), accepted_date=date(2024, 7, 15), eps=1.4, revenue=115),
                FundamentalsQuarter(report_date=date(2024, 3, 31), accepted_date=date(2024, 4, 15), eps=1.3, revenue=110),
                FundamentalsQuarter(report_date=date(2023, 9, 30), accepted_date=date(2023, 10, 15), eps=1.0, revenue=100),
                FundamentalsQuarter(report_date=date(2023, 6, 30), accepted_date=date(2023, 7, 15), eps=1.0, revenue=95),
                FundamentalsQuarter(report_date=date(2023, 3, 31), accepted_date=date(2023, 4, 15), eps=1.0, revenue=90),
            ]
        },
        annual={
            "AAA": [
                FundamentalsAnnual(report_date=date(2021, 12, 31), accepted_date=date(2022, 2, 1), eps=1.0, revenue=400),
                FundamentalsAnnual(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), eps=1.2, revenue=450),
                FundamentalsAnnual(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), eps=1.4, revenue=480),
            ]
        },
        shares={
            "AAA": [
                SharesOutstandingSnapshot(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), shares_outstanding=50_000_000)
            ]
        },
        owners={
            "AAA": [
                OwnershipSnapshot(report_date=date(2023, 12, 31), accepted_date=date(2024, 2, 1), institutional_owners=200),
                OwnershipSnapshot(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), institutional_owners=180),
            ]
        },
        releases={"AAA": [PressRelease(date_published=date(2024, 1, 1), text="New product launched.")]},
    )
    scan = scan_market(["AAA"], as_of, alpaca, fmp, FakeLlm(result=True), config, BotLog())
    assert scan.candidates


def test_prefilter_uses_screener_list_when_available() -> None:
    config = BotConfig()
    as_of = date(2024, 6, 1)
    market_bars = _bars(
        date(2024, 1, 1),
        [100, 99, 98, 99, 100, 102, 103, 104, 105, 106],
        [100, 90, 80, 85, 90, 120, 130, 125, 140, 150],
    )
    aaa_bars = _bars(date(2023, 1, 1), [20 + idx * 0.1 for idx in range(260)])
    bbb_bars = _bars(date(2023, 1, 1), [22 + idx * 0.1 for idx in range(260)])
    alpaca = CountingAlpaca(
        bars_by_symbol={"AAA": aaa_bars, "BBB": bbb_bars, "SPY": market_bars, "QQQ": market_bars},
        metadata_by_symbol={
            "AAA": SymbolMetadata(symbol="AAA", is_us_common_stock=True),
            "BBB": SymbolMetadata(symbol="BBB", is_us_common_stock=True),
        },
    )
    fmp = FakeFmp(
        quarterly={},
        annual={},
        shares={},
        owners={},
        releases={},
        screener=["AAA", "BBB"],
    )
    scan_market(["AAA", "BBB", "CCC", "DDD"], as_of, alpaca, fmp, FakeLlm(result=True), config, BotLog())
    requested = set(alpaca.symbols_requested or [])
    assert "CCC" not in requested
    assert "DDD" not in requested
    assert "AAA" in requested
    assert "BBB" in requested
    assert "SPY" in requested
    assert "QQQ" in requested


def test_prefilter_returns_empty_when_screener_returns_empty() -> None:
    config = BotConfig()
    as_of = date(2024, 6, 1)
    market_bars = _bars(
        date(2024, 1, 1),
        [100, 99, 98, 99, 100, 102, 103, 104, 105, 106],
        [100, 90, 80, 85, 90, 120, 130, 125, 140, 150],
    )
    aaa_bars = _bars(date(2023, 1, 1), [20 + idx * 0.1 for idx in range(260)])
    ccc_bars = _bars(date(2023, 1, 1), [18 + idx * 0.1 for idx in range(260)])
    alpaca = CountingAlpaca(
        bars_by_symbol={"AAA": aaa_bars, "CCC": ccc_bars, "SPY": market_bars, "QQQ": market_bars},
        metadata_by_symbol={
            "AAA": SymbolMetadata(symbol="AAA", is_us_common_stock=True),
            "CCC": SymbolMetadata(symbol="CCC", is_us_common_stock=True),
        },
    )
    fmp = FakeFmp(
        quarterly={},
        annual={},
        shares={},
        owners={},
        releases={},
        screener=[],
    )
    scan_market(["AAA", "CCC"], as_of, alpaca, fmp, FakeLlm(result=True), config, BotLog())
    requested = set(alpaca.symbols_requested or [])
    assert "AAA" not in requested
    assert "CCC" not in requested


def _sell_bars(start: date, closes: List[float]) -> List[Bar]:
    return _bars(start, closes, [1_000_000 for _ in closes])


def _sell_bars_with_volumes(start: date, closes: List[float], volumes: List[int]) -> List[Bar]:
    return _bars(start, closes, volumes)


def test_profit_take_triggers_after_hold_window() -> None:
    entry_date = date(2024, 1, 1)
    bars = _sell_bars(entry_date, [100.0 for _ in range(HOLD_DISABLE_DAYS + 10)])
    bars[-1] = Bar(
        day=bars[-1].day,
        open=120.0,
        high=122.0,
        low=119.0,
        close=121.0,
        volume=1_000_000,
    )
    log = BotLog()
    sells = evaluate_sell_signals(
        positions=[Position(symbol="AAA", qty=10, entry_price=100.0, entry_date=entry_date)],
        price_history={"AAA": bars},
        as_of=bars[-1].day,
        log=log,
    )
    assert any(order.reason == "profit-take-20" for order in sells)


def test_power_play_disables_profit_take_before_hold_window() -> None:
    entry_date = date(2024, 1, 1)
    closes = [100.0 for _ in range(POWER_PLAY_DAYS - 1)] + [120.0]
    closes.extend([121.0 for _ in range(10)])
    bars = _sell_bars(entry_date, closes)
    log = BotLog()
    sells = evaluate_sell_signals(
        positions=[Position(symbol="AAA", qty=10, entry_price=100.0, entry_date=entry_date)],
        price_history={"AAA": bars},
        as_of=bars[-1].day,
        log=log,
    )
    assert not any(order.reason == "profit-take-20" for order in sells)


def test_profit_take_after_hold_window_for_power_play() -> None:
    entry_date = date(2024, 1, 1)
    closes = [100.0 for _ in range(POWER_PLAY_DAYS - 1)] + [120.0]
    closes.extend([121.0 for _ in range(HOLD_DISABLE_DAYS)])
    bars = _sell_bars(entry_date, closes)
    log = BotLog()
    sells = evaluate_sell_signals(
        positions=[Position(symbol="AAA", qty=10, entry_price=100.0, entry_date=entry_date)],
        price_history={"AAA": bars},
        as_of=bars[-1].day,
        log=log,
    )
    assert any(order.reason == "profit-take-20" for order in sells)


def test_sma50_violation_triggers_sell() -> None:
    entry_date = date(2024, 1, 1)
    closes = [100.0 for _ in range(49)] + [90.0]
    bars = _sell_bars(entry_date, closes)
    log = BotLog()
    sells = evaluate_sell_signals(
        positions=[Position(symbol="AAA", qty=10, entry_price=100.0, entry_date=entry_date)],
        price_history={"AAA": bars},
        as_of=bars[-1].day,
        log=log,
    )
    assert any(order.reason == "sma50-violation" for order in sells)


def test_climax_top_triggers_sell() -> None:
    entry_date = date(2024, 1, 1)
    closes = [100.0 for _ in range(20)] + [130.0 for _ in range(10)] + [135.0, 140.0, 138.0]
    volumes = [1_000_000 for _ in range(len(closes) - 1)] + [2_000_000]
    bars = _sell_bars_with_volumes(entry_date, closes, volumes)
    bars[-1] = Bar(
        day=bars[-1].day,
        open=140.0,
        high=150.0,
        low=130.0,
        close=138.0,
        volume=2_000_000,
    )
    log = BotLog()
    sells = evaluate_sell_signals(
        positions=[Position(symbol="AAA", qty=10, entry_price=100.0, entry_date=entry_date)],
        price_history={"AAA": bars},
        as_of=bars[-1].day,
        log=log,
    )
    assert any(order.reason == "climax-top" for order in sells)


def test_climax_top_does_not_trigger_without_volume_spike() -> None:
    entry_date = date(2024, 1, 1)
    closes = [100.0 for _ in range(20)] + [130.0 for _ in range(10)] + [135.0, 140.0, 138.0]
    volumes = [1_000_000 for _ in range(len(closes) - 1)] + [1_200_000]
    bars = _sell_bars_with_volumes(entry_date, closes, volumes)
    bars[-1] = Bar(
        day=bars[-1].day,
        open=140.0,
        high=150.0,
        low=130.0,
        close=138.0,
        volume=1_200_000,
    )
    log = BotLog()
    sells = evaluate_sell_signals(
        positions=[Position(symbol="AAA", qty=10, entry_price=100.0, entry_date=entry_date)],
        price_history={"AAA": bars},
        as_of=bars[-1].day,
        log=log,
    )
    assert not any(order.reason == "climax-top" for order in sells)


def test_stop_loss_priority_over_sma() -> None:
    entry_date = date(2024, 1, 1)
    closes = [100.0 for _ in range(49)] + [90.0]
    bars = _sell_bars(entry_date, closes)
    log = BotLog()
    stops = check_stops(
        positions=[Position(symbol="AAA", qty=10, entry_price=100.0, entry_date=entry_date)],
        latest_prices={"AAA": 90.0},
        config=BotConfig(),
        log=log,
    )
    stop_symbols = {order.symbol for order in stops}
    sell_positions = [
        position
        for position in [Position(symbol="AAA", qty=10, entry_price=100.0, entry_date=entry_date)]
        if position.symbol not in stop_symbols
    ]
    sells = evaluate_sell_signals(
        positions=sell_positions,
        price_history={"AAA": bars},
        as_of=bars[-1].day,
        log=log,
    )
    assert stop_symbols == {"AAA"}
    assert any(order.reason == "stop-loss" for order in stops)
    assert not sells


def test_add_on_order_fires_in_uptrend_when_up_2_5pct() -> None:
    config = BotConfig()
    market_bars = _bars(
        date(2024, 1, 1),
        [100, 99, 98, 99, 100, 102, 103, 104, 105, 106],
        [100, 90, 80, 85, 90, 120, 130, 125, 140, 150],
    )
    aaa_bars = _bars(date(2023, 1, 1), [103.0 for _ in range(260)])
    alpaca = FakeAlpaca(
        bars_by_symbol={"AAA": aaa_bars, "SPY": market_bars, "QQQ": market_bars},
        metadata_by_symbol={"AAA": SymbolMetadata(symbol="AAA", is_us_common_stock=True)},
        positions=[("AAA", 10, 100.0, date(2024, 1, 1))],
    )
    fmp = FakeFmp(quarterly={}, annual={}, shares={}, owners={}, releases={})
    bot = Bot(alpaca=alpaca, fmp=fmp, llm=FakeLlm(result=True), config=config)
    orders, _log = bot.run_daily(["AAA", "SPY", "QQQ"], date(2024, 6, 1))
    assert any(order.reason == "can-slim-add" for order in orders)


def test_add_on_blocked_if_stop_loss_triggers() -> None:
    config = BotConfig()
    market_bars = _bars(
        date(2024, 1, 1),
        [100, 99, 98, 99, 100, 102, 103, 104, 105, 106],
        [100, 90, 80, 85, 90, 120, 130, 125, 140, 150],
    )
    aaa_bars = _bars(date(2023, 1, 1), [90.0 for _ in range(260)])
    alpaca = FakeAlpaca(
        bars_by_symbol={"AAA": aaa_bars, "SPY": market_bars, "QQQ": market_bars},
        metadata_by_symbol={"AAA": SymbolMetadata(symbol="AAA", is_us_common_stock=True)},
        positions=[("AAA", 10, 100.0, date(2024, 1, 1))],
    )
    fmp = FakeFmp(quarterly={}, annual={}, shares={}, owners={}, releases={})
    bot = Bot(alpaca=alpaca, fmp=fmp, llm=FakeLlm(result=True), config=config)
    orders, _log = bot.run_daily(["AAA", "SPY", "QQQ"], date(2024, 6, 1))
    assert any(order.reason == "stop-loss" for order in orders)
    assert not any(order.reason == "can-slim-add" for order in orders)


def test_add_on_blocked_if_sell_rules_triggers() -> None:
    config = BotConfig()
    market_bars = _bars(
        date(2024, 1, 1),
        [100, 99, 98, 99, 100, 102, 103, 104, 105, 106],
        [100, 90, 80, 85, 90, 120, 130, 125, 140, 150],
    )
    closes = [110.0 for _ in range(259)] + [103.0]
    aaa_bars = _bars(date(2023, 1, 1), closes)
    alpaca = FakeAlpaca(
        bars_by_symbol={"AAA": aaa_bars, "SPY": market_bars, "QQQ": market_bars},
        metadata_by_symbol={"AAA": SymbolMetadata(symbol="AAA", is_us_common_stock=True)},
        positions=[("AAA", 10, 100.0, date(2024, 1, 1))],
    )
    fmp = FakeFmp(quarterly={}, annual={}, shares={}, owners={}, releases={})
    bot = Bot(alpaca=alpaca, fmp=fmp, llm=FakeLlm(result=True), config=config)
    orders, _log = bot.run_daily(["AAA", "SPY", "QQQ"], date(2024, 6, 1))
    assert any(order.reason == "sma50-violation" for order in orders)
    assert not any(order.reason == "can-slim-add" for order in orders)
