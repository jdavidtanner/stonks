from __future__ import annotations

import csv

from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from bot.config import BotConfig
from bot.data_providers import AlpacaClient, FmpClient, LlmClient
from bot.models import Bar, FundamentalsAnnual, FundamentalsQuarter, OwnershipSnapshot, PressRelease, SharesOutstandingSnapshot, SymbolMetadata
from bot.walk_forward import WalkForwardRunner


class FakeAlpacaWalk(AlpacaClient):
    def __init__(self, bars_by_symbol: Dict[str, List[Bar]], metadata_by_symbol: Dict[str, SymbolMetadata]):
        self.bars_by_symbol = bars_by_symbol
        self.metadata_by_symbol = metadata_by_symbol

    def daily_bars(self, symbol: str, end_date: date, limit: int) -> List[Bar]:
        bars = self.bars_by_symbol.get(symbol, [])
        eligible = [bar for bar in bars if bar.day <= end_date]
        return eligible[-limit:]

    def symbol_metadata(self, symbol: str) -> Optional[SymbolMetadata]:
        return self.metadata_by_symbol.get(symbol)

    def account_equity(self) -> float:
        return 1000.0

    def open_positions(self) -> List[Tuple[str, int, float, date]]:
        return []


class FakeFmpWalk(FmpClient):
    def __init__(
        self,
        quarterly: Dict[str, List[FundamentalsQuarter]],
        annual: Dict[str, List[FundamentalsAnnual]],
        shares: Dict[str, List[SharesOutstandingSnapshot]],
        owners: Dict[str, List[OwnershipSnapshot]],
        releases: Dict[str, List[str]],
    ):
        self.quarterly = quarterly
        self.annual = annual
        self.shares = shares
        self.owners = owners
        self.releases = releases

    def quarterly_fundamentals(self, symbol: str) -> List[FundamentalsQuarter]:
        return self.quarterly.get(symbol, [])

    def annual_fundamentals(self, symbol: str) -> List[FundamentalsAnnual]:
        return self.annual.get(symbol, [])

    def shares_outstanding(self, symbol: str) -> List[SharesOutstandingSnapshot]:
        return self.shares.get(symbol, [])

    def institutional_ownership(self, symbol: str) -> List[OwnershipSnapshot]:
        return self.owners.get(symbol, [])

    def press_releases(self, symbol: str, limit: int, as_of: date) -> List[PressRelease]:
        return self.releases.get(symbol, [])[:limit]

    def stock_screener(
        self,
        *,
        price_more_than: float,
        volume_more_than: int,
        market_cap_more_than: int,
        limit: int,
    ) -> List[str]:
        raise NotImplementedError("screener unavailable")


class FakeLlmWalk(LlmClient):
    def classify_new(self, text: str) -> bool:
        return True


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


def _flat_base_series(
    start: date,
    pre_days: int,
    base_days: int,
    pivot: float,
    low: float,
    breakout_close: float,
    pre_volume: int = 1_200_000,
    base_volume: int = 800_000,
) -> List[Bar]:
    series = _bars(start, [100.0 for _ in range(pre_days)], [pre_volume for _ in range(pre_days)])
    base_start = start + timedelta(days=pre_days)
    series.extend(
        _bars(
            base_start,
            [pivot - 1 for _ in range(base_days)],
            [base_volume for _ in range(base_days)],
        )
    )
    breakout_day = base_start + timedelta(days=base_days)
    series.append(
        Bar(
            day=breakout_day,
            open=breakout_close - 1,
            high=breakout_close + 1,
            low=breakout_close - 2,
            close=breakout_close,
            volume=1_600_000,
        )
    )
    return series


def _market_bars(start: date, total_days: int) -> List[Bar]:
    base_days = total_days - 20
    closes = [100.0 for _ in range(base_days)]
    volumes = [100 for _ in range(base_days)]
    last_close = closes[-1] if closes else 100.0
    last_volume = volumes[-1] if volumes else 100
    for idx in range(20):
        last_close *= 1.015
        last_volume += 10
        closes.append(last_close)
        volumes.append(last_volume)
    return _bars(start, closes, volumes)


def test_walk_forward_market_off_no_buys() -> None:
    start = date(2024, 1, 1)
    spy = _bars(start, [100.0])
    qqq = _bars(start, [100.0])
    alpaca = FakeAlpacaWalk(
        bars_by_symbol={"SPY": spy, "QQQ": qqq},
        metadata_by_symbol={},
    )
    fmp = FakeFmpWalk(quarterly={}, annual={}, shares={}, owners={}, releases={})
    runner = WalkForwardRunner()
    result = runner.run(["AAA"], start, start + timedelta(days=4), alpaca, fmp, FakeLlmWalk(), BotConfig())
    assert result.num_buys == 0


def test_walk_forward_stop_loss_triggers() -> None:
    start = date(2023, 1, 1)
    spy = _market_bars(start, 260)
    qqq = _market_bars(start, 260)
    aaa_series = _flat_base_series(start, 220, 30, 105.0, 95.0, 110.0)
    drop_day = aaa_series[-1].day + timedelta(days=1)
    aaa_series.append(
        Bar(
            day=drop_day,
            open=90.0,
            high=92.0,
            low=88.0,
            close=90.0,
            volume=1_000_000,
        )
    )
    alpaca = FakeAlpacaWalk(
        bars_by_symbol={"AAA": aaa_series, "SPY": spy, "QQQ": qqq},
        metadata_by_symbol={"AAA": SymbolMetadata(symbol="AAA", is_us_common_stock=True)},
    )
    fmp = FakeFmpWalk(
        quarterly={
            "AAA": [
                FundamentalsQuarter(report_date=date(2023, 6, 30), accepted_date=date(2023, 7, 1), eps=1.5, revenue=120),
                FundamentalsQuarter(report_date=date(2023, 3, 31), accepted_date=date(2023, 4, 1), eps=1.4, revenue=115),
                FundamentalsQuarter(report_date=date(2022, 12, 31), accepted_date=date(2023, 1, 1), eps=1.3, revenue=110),
                FundamentalsQuarter(report_date=date(2022, 6, 30), accepted_date=date(2022, 7, 1), eps=1.0, revenue=100),
                FundamentalsQuarter(report_date=date(2022, 3, 31), accepted_date=date(2022, 4, 1), eps=1.0, revenue=95),
                FundamentalsQuarter(report_date=date(2021, 12, 31), accepted_date=date(2022, 1, 1), eps=1.0, revenue=90),
            ]
        },
        annual={
            "AAA": [
                FundamentalsAnnual(report_date=date(2021, 12, 31), accepted_date=date(2022, 2, 1), eps=1.0, revenue=400),
                FundamentalsAnnual(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), eps=1.2, revenue=450),
                FundamentalsAnnual(report_date=date(2023, 12, 31), accepted_date=date(2023, 2, 1), eps=1.4, revenue=480),
            ]
        },
        shares={
            "AAA": [
                SharesOutstandingSnapshot(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), shares_outstanding=50_000_000)
            ]
        },
        owners={
            "AAA": [
                OwnershipSnapshot(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), institutional_owners=200),
                OwnershipSnapshot(report_date=date(2021, 12, 31), accepted_date=date(2022, 2, 1), institutional_owners=180),
            ]
        },
        releases={"AAA": [PressRelease(published_date=date(2020, 1, 1), text="New product launched.")]},
    )
    runner = WalkForwardRunner()
    result = runner.run(
        ["AAA"],
        aaa_series[-1].day,
        drop_day + timedelta(days=1),
        alpaca,
        fmp,
        FakeLlmWalk(),
        BotConfig(),
    )
    assert any(order.reason == "stop-loss" for orders in result.orders_by_day.values() for order in orders)


def test_walk_forward_no_lookahead_fill_next_day() -> None:
    start = date(2023, 1, 1)
    spy = _market_bars(start, 260)
    qqq = _market_bars(start, 260)
    aaa_series = _flat_base_series(start, 220, 30, 105.0, 95.0, 110.0)
    next_day = aaa_series[-1].day + timedelta(days=1)
    aaa_series.append(
        Bar(
            day=next_day,
            open=107.0,
            high=108.0,
            low=106.0,
            close=107.0,
            volume=1_000_000,
        )
    )
    alpaca = FakeAlpacaWalk(
        bars_by_symbol={"AAA": aaa_series, "SPY": spy, "QQQ": qqq},
        metadata_by_symbol={"AAA": SymbolMetadata(symbol="AAA", is_us_common_stock=True)},
    )
    fmp = FakeFmpWalk(
        quarterly={
            "AAA": [
                FundamentalsQuarter(report_date=date(2023, 6, 30), accepted_date=date(2023, 7, 1), eps=1.5, revenue=120),
                FundamentalsQuarter(report_date=date(2023, 3, 31), accepted_date=date(2023, 4, 1), eps=1.4, revenue=115),
                FundamentalsQuarter(report_date=date(2022, 12, 31), accepted_date=date(2023, 1, 1), eps=1.3, revenue=110),
                FundamentalsQuarter(report_date=date(2022, 6, 30), accepted_date=date(2022, 7, 1), eps=1.0, revenue=100),
                FundamentalsQuarter(report_date=date(2022, 3, 31), accepted_date=date(2022, 4, 1), eps=1.0, revenue=95),
                FundamentalsQuarter(report_date=date(2021, 12, 31), accepted_date=date(2022, 1, 1), eps=1.0, revenue=90),
            ]
        },
        annual={
            "AAA": [
                FundamentalsAnnual(report_date=date(2021, 12, 31), accepted_date=date(2022, 2, 1), eps=1.0, revenue=400),
                FundamentalsAnnual(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), eps=1.2, revenue=450),
                FundamentalsAnnual(report_date=date(2023, 12, 31), accepted_date=date(2023, 2, 1), eps=1.4, revenue=480),
            ]
        },
        shares={
            "AAA": [
                SharesOutstandingSnapshot(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), shares_outstanding=50_000_000)
            ]
        },
        owners={
            "AAA": [
                OwnershipSnapshot(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), institutional_owners=200),
                OwnershipSnapshot(report_date=date(2021, 12, 31), accepted_date=date(2022, 2, 1), institutional_owners=180),
            ]
        },
        releases={"AAA": [PressRelease(published_date=date(2020, 1, 1), text="New product launched.")]},
    )
    runner = WalkForwardRunner()
    result = runner.run(
        ["AAA"],
        aaa_series[-1].day,
        next_day,
        alpaca,
        fmp,
        FakeLlmWalk(),
        BotConfig(),
    )
    assert result.fills
    assert result.fills[0].day == next_day


def _cost_fixture(
    tail: Optional[List[Bar]] = None,
) -> Tuple[FakeAlpacaWalk, FakeFmpWalk, date, date]:
    start = date(2023, 1, 1)
    spy = _market_bars(start, 260)
    aaa_series = _flat_base_series(start, 220, 30, 105.0, 95.0, 110.0)
    next_day = aaa_series[-1].day + timedelta(days=1)
    aaa_series.append(
        Bar(day=next_day, open=107.0, high=108.0, low=106.0, close=107.0, volume=1_000_000)
    )
    start_day = aaa_series[-2].day
    if tail:
        aaa_series.extend(tail)
        next_day = tail[-1].day
    alpaca = FakeAlpacaWalk(
        bars_by_symbol={"AAA": aaa_series, "SPY": spy, "QQQ": _market_bars(start, 260)},
        metadata_by_symbol={"AAA": SymbolMetadata(symbol="AAA", is_us_common_stock=True)},
    )
    fmp = FakeFmpWalk(
        quarterly={
            "AAA": [
                FundamentalsQuarter(report_date=date(2023, 6, 30), accepted_date=date(2023, 7, 1), eps=1.5, revenue=120),
                FundamentalsQuarter(report_date=date(2023, 3, 31), accepted_date=date(2023, 4, 1), eps=1.4, revenue=115),
                FundamentalsQuarter(report_date=date(2022, 12, 31), accepted_date=date(2023, 1, 1), eps=1.3, revenue=110),
                FundamentalsQuarter(report_date=date(2022, 6, 30), accepted_date=date(2022, 7, 1), eps=1.0, revenue=100),
                FundamentalsQuarter(report_date=date(2022, 3, 31), accepted_date=date(2022, 4, 1), eps=1.0, revenue=95),
                FundamentalsQuarter(report_date=date(2021, 12, 31), accepted_date=date(2022, 1, 1), eps=1.0, revenue=90),
            ]
        },
        annual={
            "AAA": [
                FundamentalsAnnual(report_date=date(2021, 12, 31), accepted_date=date(2022, 2, 1), eps=1.0, revenue=400),
                FundamentalsAnnual(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), eps=1.2, revenue=450),
                FundamentalsAnnual(report_date=date(2023, 12, 31), accepted_date=date(2023, 2, 1), eps=1.4, revenue=480),
            ]
        },
        shares={
            "AAA": [
                SharesOutstandingSnapshot(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), shares_outstanding=50_000_000)
            ]
        },
        owners={
            "AAA": [
                OwnershipSnapshot(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), institutional_owners=200),
                OwnershipSnapshot(report_date=date(2021, 12, 31), accepted_date=date(2022, 2, 1), institutional_owners=180),
            ]
        },
        releases={"AAA": [PressRelease(published_date=date(2020, 1, 1), text="New product launched.")]},
    )
    return alpaca, fmp, start_day, next_day


def _run_with(config: BotConfig, tail: Optional[List[Bar]] = None, output_dir=None):
    alpaca, fmp, start, end = _cost_fixture(tail)
    return WalkForwardRunner().run(
        ["AAA"], start, end, alpaca, fmp, FakeLlmWalk(), config, output_dir=output_dir
    )


def _drop_tail(after: date) -> List[Bar]:
    """A hard drop that trips the 8% stop, then a day for the sell to fill on."""
    drop_day = after + timedelta(days=1)
    return [
        Bar(day=drop_day, open=90.0, high=92.0, low=88.0, close=90.0, volume=1_000_000),
        Bar(
            day=drop_day + timedelta(days=1),
            open=89.0,
            high=91.0,
            low=87.0,
            close=89.0,
            volume=1_000_000,
        ),
    ]


def test_attribution_identity_and_benchmark() -> None:
    result = _run_with(BotConfig(commission_per_trade=1.0, slippage_pct=0.01))
    attribution = result.attribution
    assert attribution is not None
    assert result.fills, "need at least one fill for costs to show up"
    # gross minus the costs paid has to land exactly on net
    assert abs(
        attribution.gross_pnl
        - attribution.commission
        - attribution.slippage
        - attribution.net_pnl
    ) < 1e-6
    assert attribution.commission > 0
    assert attribution.slippage > 0
    assert attribution.benchmark_return is not None
    assert abs(
        attribution.excess_return
        - (attribution.net_return - attribution.benchmark_return)
    ) < 1e-9


def test_costs_reduce_net_return() -> None:
    free = _run_with(BotConfig())
    costed = _run_with(BotConfig(commission_per_trade=1.0, slippage_pct=0.01))
    assert free.attribution.commission == 0.0
    assert free.attribution.slippage == 0.0
    assert costed.attribution.net_return < free.attribution.net_return


def test_sell_side_charges_slippage_and_commission() -> None:
    result = _run_with(
        BotConfig(commission_per_trade=1.0, slippage_pct=0.01),
        tail=_drop_tail(date(2023, 9, 9)),
    )
    sells = [fill for fill in result.fills if fill.side == "sell"]
    buys = [fill for fill in result.fills if fill.side == "buy"]
    assert len(sells) == 1 and len(buys) == 1
    sell = sells[0]
    # sell fills 1% BELOW the reference open of 89.0, not above it
    assert abs(sell.price - 89.0 * 0.99) < 1e-9
    attribution = result.attribution
    assert attribution.commission == 2.0  # one buy, one sell
    expected_slippage = buys[0].qty * 110.0 * 0.01 + sell.qty * 89.0 * 0.01
    assert abs(attribution.slippage - expected_slippage) < 1e-9
    assert abs(
        attribution.gross_pnl
        - attribution.commission
        - attribution.slippage
        - attribution.net_pnl
    ) < 1e-6
    # the costs have to actually leave cash, not just get counted in the report
    expected_equity = (
        1000.0
        - (buys[0].price * buys[0].qty + 1.0)
        + (sell.price * sell.qty - 1.0)
    )
    assert abs(result.final_equity - expected_equity) < 1e-6


def test_benchmark_return_matches_spy_closes() -> None:
    alpaca, fmp, start, end = _cost_fixture()
    result = WalkForwardRunner().run(
        ["AAA"], start, end, alpaca, fmp, FakeLlmWalk(), BotConfig()
    )
    first = alpaca.daily_bars("SPY", end_date=start, limit=1)[-1].close
    last = alpaca.daily_bars("SPY", end_date=end, limit=1)[-1].close
    assert first != last, "flat SPY would make this assertion vacuous"
    assert abs(result.attribution.benchmark_return - (last - first) / first) < 1e-12


def test_attribution_csv_is_written(tmp_path) -> None:
    result = _run_with(
        BotConfig(commission_per_trade=1.0, slippage_pct=0.01),
        tail=_drop_tail(date(2023, 9, 9)),
        output_dir=tmp_path,
    )
    path = tmp_path / "attribution.csv"
    assert path.exists(), "_write_reports swallows exceptions, so check the file itself"
    rows = dict(csv.reader(path.read_text(encoding="utf-8").splitlines())  # type: ignore[arg-type]
                )
    assert rows["component"] == "value"
    # costs are written as negatives, they came out of the return
    assert float(rows["commission"]) == -result.attribution.commission
    assert abs(float(rows["slippage"]) + result.attribution.slippage) < 1e-4  # file is 4dp
    assert abs(float(rows["net_pnl"]) - result.attribution.net_pnl) < 0.01
    assert abs(float(rows["excess_return"]) - result.attribution.excess_return) < 1e-4
