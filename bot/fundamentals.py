from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, List, Optional, Tuple

from bot.data_providers import FmpClient
from bot.logger import BotLog
from bot.models import FundamentalsAnnual, FundamentalsQuarter


@dataclass(frozen=True)
class FundamentalsResult:
    symbol: str
    quarterly_eps_growth: float
    quarterly_revenue_growth: float
    annual_eps_cagr: Optional[float]
    annual_revenue_trend_ok: bool
    eps_growth_last3: Tuple[float, float, float]


def _latest_quarter(quarters: List[FundamentalsQuarter], as_of: date) -> Optional[FundamentalsQuarter]:
    eligible = [q for q in quarters if q.accepted_date <= as_of]
    if not eligible:
        return None
    return max(eligible, key=lambda q: q.accepted_date)


def _quarter_year_ago(
    quarters: List[FundamentalsQuarter], latest: FundamentalsQuarter
) -> Optional[FundamentalsQuarter]:
    candidates = [
        q
        for q in quarters
        if q.accepted_date < latest.accepted_date
        and q.report_date.year == latest.report_date.year - 1
        and q.report_date.month == latest.report_date.month
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda q: q.accepted_date)


def _latest_quarters(
    quarters: List[FundamentalsQuarter], as_of: date, count: int
) -> List[FundamentalsQuarter]:
    eligible = [q for q in quarters if q.accepted_date <= as_of]
    eligible.sort(key=lambda q: q.accepted_date)
    return eligible[-count:] if len(eligible) >= count else []


def _annual_series(annual: List[FundamentalsAnnual], as_of: date) -> List[FundamentalsAnnual]:
    eligible = [a for a in annual if a.accepted_date <= as_of]
    return sorted(eligible, key=lambda a: a.report_date)


def _cagr(start: float, end: float, years: int) -> Optional[float]:
    if start <= 0 or years <= 0:
        return None
    return (end / start) ** (1 / years) - 1


def evaluate_fundamentals(
    symbol: str,
    as_of: date,
    fmp: FmpClient,
    log: BotLog,
) -> Optional[FundamentalsResult]:
    quarters = fmp.quarterly_fundamentals(symbol)
    annual = fmp.annual_fundamentals(symbol)

    latest_quarter = _latest_quarter(quarters, as_of)
    if latest_quarter is None:
        log.info(f"{symbol} rejected: missing quarterly EPS (no acceptedDate).")
        return None
    year_ago = _quarter_year_ago(quarters, latest_quarter)
    if year_ago is None:
        log.info(f"{symbol} rejected: missing year-ago EPS for YoY comparison.")
        return None
    if year_ago.eps == 0:
        log.info(f"{symbol} rejected: prior EPS zero for YoY calculation.")
        return None
    quarterly_growth = (latest_quarter.eps - year_ago.eps) / abs(year_ago.eps)
    if quarterly_growth < 0.20:
        log.info(f"{symbol} rejected: quarterly EPS growth {quarterly_growth:.2%} < 20%.")
        return None
    if year_ago.revenue == 0:
        log.info(f"{symbol} rejected: prior revenue zero for YoY calculation.")
        return None
    quarterly_revenue_growth = (latest_quarter.revenue - year_ago.revenue) / abs(year_ago.revenue)
    if quarterly_revenue_growth <= 0:
        log.info(
            f"{symbol} rejected: quarterly revenue YoY growth {quarterly_revenue_growth:.2%} <= 0."
        )
        return None
    if quarterly_growth > 2.0 and quarterly_revenue_growth < 0.20:
        log.info(
            f"{symbol} rejected: EPS spike with weak revenue (distortion guard)."
        )
        return None
    recent_quarters = _latest_quarters(quarters, as_of, 3)
    if len(recent_quarters) < 3:
        log.info(f"{symbol} rejected: insufficient quarterly history for acceleration.")
        return None
    q0, q1, q2 = recent_quarters[::-1]
    q0_yoy = _quarter_year_ago(quarters, q0)
    q1_yoy = _quarter_year_ago(quarters, q1)
    q2_yoy = _quarter_year_ago(quarters, q2)
    if q0_yoy is None or q1_yoy is None or q2_yoy is None:
        log.info(f"{symbol} rejected: missing year-ago EPS for acceleration.")
        return None
    if q0_yoy.eps == 0 or q1_yoy.eps == 0 or q2_yoy.eps == 0:
        log.info(f"{symbol} rejected: prior EPS zero for acceleration calculation.")
        return None
    g0 = (q0.eps - q0_yoy.eps) / abs(q0_yoy.eps)
    g1 = (q1.eps - q1_yoy.eps) / abs(q1_yoy.eps)
    g2 = (q2.eps - q2_yoy.eps) / abs(q2_yoy.eps)
    if not (g0 >= g1 and g1 >= g2 and g0 >= 0.20):
        log.info(
            f"{symbol} rejected: EPS acceleration failed (g0={g0:.2%}, g1={g1:.2%}, g2={g2:.2%})."
        )
        return None

    annual_series = _annual_series(annual, as_of)
    if len(annual_series) < 3:
        log.info(f"{symbol} rejected: insufficient annual EPS history.")
        return None
    start = annual_series[0].eps
    end = annual_series[-1].eps
    if start <= 0 or end <= 0 or end <= start:
        log.info(f"{symbol} rejected: annual EPS growth not positive over 3+ years.")
        return None
    years = annual_series[-1].report_date.year - annual_series[0].report_date.year
    annual_cagr = _cagr(start, end, years) if years > 0 else None
    if annual_cagr is not None:
        log.info(f"{symbol} annual EPS CAGR computed: {annual_cagr:.2%}.")
    annual_revenues = [item.revenue for item in annual_series]
    if any(revenue <= 0 for revenue in annual_revenues):
        log.info(f"{symbol} rejected: annual revenue trend not positive (non-positive).")
        return None
    annual_revenue_trend_ok = annual_revenues[-1] > annual_revenues[0]
    if not annual_revenue_trend_ok:
        log.info(f"{symbol} rejected: annual revenue trend not positive.")
        return None
    latest_annual = annual_series[-1]
    prior_annual = annual_series[-2]
    if latest_annual.net_income is not None and prior_annual.net_income is not None:
        if latest_annual.revenue <= 0 or prior_annual.revenue <= 0:
            log.info(f"{symbol} rejected: annual revenue non-positive for margin check.")
            return None
        latest_margin = latest_annual.net_income / latest_annual.revenue
        prior_margin = prior_annual.net_income / prior_annual.revenue
        if latest_margin <= 0 or prior_margin <= 0:
            log.info(f"{symbol} rejected: annual net income margin non-positive.")
            return None
        if latest_margin < prior_margin * 0.5:
            log.info(f"{symbol} rejected: annual net income margin collapse.")
            return None

    return FundamentalsResult(
        symbol=symbol,
        quarterly_eps_growth=quarterly_growth,
        quarterly_revenue_growth=quarterly_revenue_growth,
        annual_eps_cagr=annual_cagr,
        annual_revenue_trend_ok=annual_revenue_trend_ok,
        eps_growth_last3=(g0, g1, g2),
    )
