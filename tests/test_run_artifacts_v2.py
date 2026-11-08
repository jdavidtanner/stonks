from __future__ import annotations

import csv
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from bot.config import BotConfig
from bot.execute_paper import execute_paper
from bot.models import (
    Bar,
    FundamentalsAnnual,
    FundamentalsQuarter,
    OwnershipSnapshot,
    PressRelease,
    SharesOutstandingSnapshot,
    SymbolMetadata,
)
from bot.paper_guard import require_paper_trading


class FakeAlpacaPaper:
    def __init__(
        self,
        bars_by_symbol: Dict[str, List[Bar]],
        metadata_by_symbol: Dict[str, SymbolMetadata],
        base_url: str = "https://paper-api.alpaca.markets",
    ) -> None:
        self.bars_by_symbol = bars_by_symbol
        self.metadata_by_symbol = metadata_by_symbol
        self.base_url = base_url

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

    def assert_paper_trading(self) -> None:
        require_paper_trading(self.base_url)

    def submit_order_with_response(self, symbol: str, qty: int, side: str) -> Dict[str, object]:
        return {"ok": True, "response": {"id": f"{symbol}-{side}"}}


class FakeFmpPaper:
    def __init__(
        self,
        quarterly: Dict[str, List[FundamentalsQuarter]],
        annual: Dict[str, List[FundamentalsAnnual]],
        shares: Dict[str, List[SharesOutstandingSnapshot]],
        owners: Dict[str, List[OwnershipSnapshot]],
        releases: Dict[str, List[str]],
    ) -> None:
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


class FakeLlmPaper:
    def classify_new(self, text: str) -> bool:
        return True


def _bars(start: date, closes: List[float], volumes: Optional[List[int]] = None) -> List[Bar]:
    if volumes is None:
        volumes = [1_200_000 for _ in closes]
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


def _flat_base_series(start: date, pre_days: int, base_days: int, pivot: float) -> List[Bar]:
    pre_volumes = [1_200_000 for _ in range(pre_days)]
    base_volumes = [800_000 for _ in range(base_days)]
    series = _bars(start, [100.0 for _ in range(pre_days)], pre_volumes)
    base_start = start + timedelta(days=pre_days)
    series.extend(_bars(base_start, [pivot - 1 for _ in range(base_days)], base_volumes))
    breakout_day = base_start + timedelta(days=base_days)
    series.append(
        Bar(
            day=breakout_day,
            open=pivot + 4,
            high=pivot + 6,
            low=pivot + 3,
            close=pivot + 5,
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
    for _ in range(20):
        last_close *= 1.015
        last_volume += 10
        closes.append(last_close)
        volumes.append(last_volume)
    return _bars(start, closes, volumes)


def _build_fmp(symbol: str) -> FakeFmpPaper:
    return FakeFmpPaper(
        quarterly={
            symbol: [
                FundamentalsQuarter(report_date=date(2022, 9, 30), accepted_date=date(2022, 10, 1), eps=2.0, revenue=100),
                FundamentalsQuarter(report_date=date(2021, 9, 30), accepted_date=date(2021, 10, 1), eps=1.0, revenue=90),
            ]
        },
        annual={
            symbol: [
                FundamentalsAnnual(report_date=date(2021, 12, 31), accepted_date=date(2022, 2, 1), eps=1.0, revenue=400),
                FundamentalsAnnual(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), eps=1.2, revenue=450),
                FundamentalsAnnual(report_date=date(2023, 12, 31), accepted_date=date(2023, 2, 1), eps=1.4, revenue=480),
            ]
        },
        shares={
            symbol: [
                SharesOutstandingSnapshot(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), shares_outstanding=50_000_000)
            ]
        },
        owners={
            symbol: [
                OwnershipSnapshot(report_date=date(2022, 12, 31), accepted_date=date(2023, 2, 1), institutional_owners=200)
            ]
        },
        releases={symbol: [PressRelease(published_date=date(2020, 1, 1), text="New product launched.")]},
    )


def test_execute_paper_writes_artifacts_v2(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("REQUIRE_PAPER_ACK", "I_UNDERSTAND_THIS_IS_PAPER")
    start = date(2023, 1, 1)
    spy = _market_bars(start, 260)
    qqq = _market_bars(start, 260)
    aaa_series = _flat_base_series(start, 220, 30, 105.0)
    alpaca = FakeAlpacaPaper(
        bars_by_symbol={"AAA": aaa_series, "SPY": spy, "QQQ": qqq},
        metadata_by_symbol={"AAA": SymbolMetadata(symbol="AAA", is_us_common_stock=True)},
    )
    fmp = _build_fmp("AAA")
    result = execute_paper(
        ["AAA"],
        aaa_series[-1].day,
        alpaca,
        fmp,
        FakeLlmPaper(),
        BotConfig(),
        confirm=False,
        runs_dir=tmp_path,
    )
    assert result == 0
    date_dir = tmp_path / aaa_series[-1].day.isoformat()
    run_dirs = list(date_dir.glob("run_*"))
    assert run_dirs
    run_dir = run_dirs[0]
    assert (run_dir / "orders.csv").exists()
    assert (run_dir / "candidates.csv").exists()
    assert (run_dir / "market.json").exists()
    assert (run_dir / "summary.json").exists()

    with (run_dir / "orders.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        headers = next(reader)
    assert headers == ["date", "symbol", "side", "qty", "reason"]

    with (run_dir / "candidates.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        headers = next(reader)
    assert headers == ["date", "symbol", "rs_percentile", "pivot", "close"]

    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert "reason_counts" in summary
    assert "buy_count" in summary
    assert "sell_count" in summary
    assert "add_count" in summary
