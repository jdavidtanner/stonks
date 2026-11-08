from __future__ import annotations

from datetime import date, timedelta
from typing import List

from bot.logger import BotLog
from bot.models import Bar
from bot.pattern_gate import detect_base_and_pivot


def _bars(
    start: date,
    days: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: int,
) -> List[Bar]:
    return [
        Bar(
            day=start + timedelta(days=idx),
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )
        for idx in range(days)
    ]


def _flat_base_series(
    start: date,
    pre_days: int,
    base_days: int,
    pivot: float,
    low: float,
    breakout_close: float,
    breakout_volume: int,
    base_volume: int,
    pre_volume: int | None = None,
) -> List[Bar]:
    if pre_volume is None:
        pre_volume = base_volume
    series: List[Bar] = []
    series.extend(_bars(start, pre_days, 200.0, 200.0, 190.0, 195.0, pre_volume))
    base_start = start + timedelta(days=pre_days)
    series.extend(_bars(base_start, base_days, pivot - 2, pivot, low, pivot - 1, base_volume))
    breakout_day = base_start + timedelta(days=base_days)
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


def _flat_base_series_custom(
    start: date,
    pre_days: int,
    base_days: int,
    pivot: float,
    low: float,
    breakout_close: float,
    breakout_volume: int,
    pre_volume: int,
    base_volume: int,
    base_closes: List[float],
) -> List[Bar]:
    series: List[Bar] = []
    series.extend(_bars(start, pre_days, 200.0, 200.0, 190.0, 195.0, pre_volume))
    base_start = start + timedelta(days=pre_days)
    for idx in range(base_days):
        close = base_closes[idx]
        series.append(
            Bar(
                day=base_start + timedelta(days=idx),
                open=pivot - 2,
                high=pivot,
                low=low,
                close=close,
                volume=base_volume,
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
            volume=breakout_volume,
        )
    )
    return series


def test_flat_base_pass_case() -> None:
    start = date(2024, 1, 1)
    series = _flat_base_series(
        start=start,
        pre_days=60,
        base_days=25,
        pivot=105.0,
        low=95.0,
        breakout_close=106.0,
        breakout_volume=1_600_000,
        base_volume=800_000,
        pre_volume=1_200_000,
    )
    as_of = series[-1].day
    log = BotLog()
    pattern = detect_base_and_pivot("AAA", series, as_of, log)
    assert pattern is not None
    assert pattern.pattern_type == "flat_base"


def test_flat_base_rejects_delayed_breakout() -> None:
    start = date(2024, 1, 1)
    series = _flat_base_series(
        start=start,
        pre_days=60,
        base_days=25,
        pivot=105.0,
        low=95.0,
        breakout_close=100.0,
        breakout_volume=1_000_000,
        base_volume=800_000,
        pre_volume=1_200_000,
    )
    old_base_end = series[-1].day
    volatile_start = old_base_end + timedelta(days=1)
    series.extend(_bars(volatile_start, 30, 140.0, 140.0, 90.0, 120.0, 1_000_000))
    as_of = series[-1].day
    series[-1] = Bar(
        day=as_of,
        open=105.0,
        high=107.0,
        low=104.0,
        close=106.0,
        volume=1_600_000,
    )
    log = BotLog()
    pattern = detect_base_and_pivot("AAA", series, as_of, log)
    assert pattern is None
    assert log.entries == ["AAA rejected: FLAT_BASE_BASE_NOT_ADJACENT"]


def test_flat_base_rejects_insufficient_history() -> None:
    start = date(2024, 1, 1)
    series = _flat_base_series(
        start=start,
        pre_days=10,
        base_days=25,
        pivot=105.0,
        low=95.0,
        breakout_close=106.0,
        breakout_volume=1_600_000,
        base_volume=800_000,
        pre_volume=1_200_000,
    )
    as_of = series[-1].day
    log = BotLog()
    pattern = detect_base_and_pivot("AAA", series, as_of, log)
    assert pattern is None
    assert log.entries == ["AAA rejected: FLAT_BASE_INSUFFICIENT_HISTORY"]


def test_flat_base_rejects_weak_breakout_volume() -> None:
    start = date(2024, 1, 1)
    series = _flat_base_series(
        start=start,
        pre_days=60,
        base_days=25,
        pivot=105.0,
        low=95.0,
        breakout_close=106.0,
        breakout_volume=1_400_000,
        base_volume=800_000,
        pre_volume=1_200_000,
    )
    as_of = series[-1].day
    log = BotLog()
    pattern = detect_base_and_pivot("AAA", series, as_of, log)
    assert pattern is None
    assert log.entries == ["AAA rejected: FLAT_BASE_WEAK_BREAKOUT_VOLUME"]


def test_flat_base_rejects_too_deep() -> None:
    start = date(2024, 1, 1)
    series = _flat_base_series(
        start=start,
        pre_days=60,
        base_days=25,
        pivot=120.0,
        low=90.0,
        breakout_close=121.0,
        breakout_volume=1_600_000,
        base_volume=800_000,
        pre_volume=1_200_000,
    )
    as_of = series[-1].day
    log = BotLog()
    pattern = detect_base_and_pivot("AAA", series, as_of, log)
    assert pattern is None
    assert log.entries == ["AAA rejected: FLAT_BASE_TOO_DEEP"]


def test_flat_base_rejects_without_volume_dry_up() -> None:
    start = date(2024, 1, 1)
    base_closes = [104.0 for _ in range(25)]
    series = _flat_base_series_custom(
        start=start,
        pre_days=60,
        base_days=25,
        pivot=105.0,
        low=95.0,
        breakout_close=106.0,
        breakout_volume=1_600_000,
        pre_volume=500_000,
        base_volume=1_500_000,
        base_closes=base_closes,
    )
    as_of = series[-1].day
    log = BotLog()
    pattern = detect_base_and_pivot("AAA", series, as_of, log)
    assert pattern is None
    assert log.entries == ["AAA rejected: FLAT_BASE_NO_DRY_UP"]


def test_flat_base_rejects_not_tight() -> None:
    start = date(2024, 1, 1)
    base_closes = [95.0 + idx * (10.0 / 24) for idx in range(25)]
    series = _flat_base_series_custom(
        start=start,
        pre_days=60,
        base_days=25,
        pivot=105.0,
        low=95.0,
        breakout_close=106.0,
        breakout_volume=1_600_000,
        pre_volume=1_500_000,
        base_volume=900_000,
        base_closes=base_closes,
    )
    as_of = series[-1].day
    log = BotLog()
    pattern = detect_base_and_pivot("AAA", series, as_of, log)
    assert pattern is None
    assert log.entries == ["AAA rejected: FLAT_BASE_NOT_TIGHT"]
