from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List

import requests

from bot.cache import RunCache
from bot.data_providers import FmpClient
from bot.logger import BotLog
from bot.models import (
    FundamentalsAnnual,
    FundamentalsQuarter,
    OwnershipSnapshot,
    SharesOutstandingSnapshot,
)


@dataclass
class FmpLive(FmpClient):
    api_key: str
    log: BotLog
    timeout_s: float = 10.0
    fail_closed: bool = False

    def __post_init__(self) -> None:
        self._cache = RunCache()

    def _request(self, path: str, params: Dict[str, str]) -> List[Dict[str, Any]]:
        url = f"https://financialmodelingprep.com/api/v3/{path}"
        params = dict(params)
        params["apikey"] = self.api_key
        for attempt in range(2):
            try:
                response = requests.get(url, params=params, timeout=self.timeout_s)
            except requests.RequestException as exc:
                message = f"FMP request failed: {exc}"
                if self.fail_closed:
                    self.log.error(message)
                    raise RuntimeError(message)
                self.log.warn(message)
                continue
            if response.status_code == 429:
                message = "FMP rate limit hit; failing closed."
                if self.fail_closed:
                    self.log.error(message)
                    raise RuntimeError(message)
                self.log.warn(message)
                return []
            if response.ok:
                data = response.json()
                if isinstance(data, list):
                    return data
                return []
            message = f"FMP API error: {response.status_code} {response.text}"
            if self.fail_closed:
                self.log.error(message)
                raise RuntimeError(message)
            self.log.warn(message)
        if self.fail_closed:
            raise RuntimeError("FMP request failed")
        return []

    def quarterly_fundamentals(self, symbol: str) -> List[FundamentalsQuarter]:
        def fetch() -> List[FundamentalsQuarter]:
            rows = self._request(
                f"income-statement/{symbol}", {"period": "quarter", "limit": "8"}
            )
            results: List[FundamentalsQuarter] = []
            for row in rows:
                accepted = row.get("acceptedDate")
                report_date = row.get("date")
                eps = row.get("eps")
                revenue = row.get("revenue")
                net_income = row.get("netIncome")
                if not (accepted and report_date and eps is not None and revenue is not None):
                    continue
                try:
                    accepted_date = datetime.fromisoformat(accepted.replace("Z", "+00:00")).date()
                    report = date.fromisoformat(report_date)
                    results.append(
                        FundamentalsQuarter(
                            report_date=report,
                            accepted_date=accepted_date,
                            eps=float(eps),
                            revenue=float(revenue),
                            net_income=float(net_income) if net_income is not None else None,
                        )
                    )
                except (ValueError, TypeError):
                    continue
            return results

        return self._cache.get_or_set(("quarterly", symbol), fetch)

    def annual_fundamentals(self, symbol: str) -> List[FundamentalsAnnual]:
        def fetch() -> List[FundamentalsAnnual]:
            rows = self._request(
                f"income-statement/{symbol}", {"period": "annual", "limit": "8"}
            )
            results: List[FundamentalsAnnual] = []
            for row in rows:
                accepted = row.get("acceptedDate")
                report_date = row.get("date")
                eps = row.get("eps")
                revenue = row.get("revenue")
                net_income = row.get("netIncome")
                if not (accepted and report_date and eps is not None and revenue is not None):
                    continue
                try:
                    accepted_date = datetime.fromisoformat(accepted.replace("Z", "+00:00")).date()
                    report = date.fromisoformat(report_date)
                    results.append(
                        FundamentalsAnnual(
                            report_date=report,
                            accepted_date=accepted_date,
                            eps=float(eps),
                            revenue=float(revenue),
                            net_income=float(net_income) if net_income is not None else None,
                        )
                    )
                except (ValueError, TypeError):
                    continue
            return results

        return self._cache.get_or_set(("annual", symbol), fetch)

    def shares_outstanding(self, symbol: str) -> List[SharesOutstandingSnapshot]:
        def fetch() -> List[SharesOutstandingSnapshot]:
            rows = self._request(f"key-metrics/{symbol}", {"period": "annual", "limit": "5"})
            results: List[SharesOutstandingSnapshot] = []
            for row in rows:
                accepted = row.get("acceptedDate")
                report_date = row.get("date")
                shares = row.get("sharesOutstanding")
                if not (accepted and report_date and shares is not None):
                    continue
                try:
                    accepted_date = datetime.fromisoformat(accepted.replace("Z", "+00:00")).date()
                    report = date.fromisoformat(report_date)
                    results.append(
                        SharesOutstandingSnapshot(
                            report_date=report,
                            accepted_date=accepted_date,
                            shares_outstanding=int(float(shares)),
                        )
                    )
                except (ValueError, TypeError):
                    continue
            return results

        return self._cache.get_or_set(("shares", symbol), fetch)

    def institutional_ownership(self, symbol: str) -> List[OwnershipSnapshot]:
        def fetch() -> List[OwnershipSnapshot]:
            rows = self._request(f"institutional-ownership/{symbol}", {"limit": "5"})
            results: List[OwnershipSnapshot] = []
            for row in rows:
                accepted = row.get("acceptedDate")
                report_date = row.get("date")
                count = row.get("investorCount")
                if not (accepted and report_date and count is not None):
                    continue
                try:
                    accepted_date = datetime.fromisoformat(accepted.replace("Z", "+00:00")).date()
                    report = date.fromisoformat(report_date)
                    results.append(
                        OwnershipSnapshot(
                            report_date=report,
                            accepted_date=accepted_date,
                            institutional_owners=int(count),
                        )
                    )
                except (ValueError, TypeError):
                    continue
            return results

        return self._cache.get_or_set(("owners", symbol), fetch)

    def press_releases(self, symbol: str, limit: int) -> List[str]:
        def fetch() -> List[str]:
            rows = self._request(f"press-releases/{symbol}", {"limit": str(limit)})
            results: List[str] = []
            for row in rows:
                text = row.get("text") or row.get("content")
                title = row.get("title")
                if not text and not title:
                    continue
                combined = f"{title}\n{text}" if text and title else text or title
                if combined:
                    results.append(combined)
            return results

        return self._cache.get_or_set(("press", symbol, limit), fetch)

    def stock_screener(
        self,
        *,
        price_more_than: float,
        volume_more_than: int,
        market_cap_more_than: int,
        limit: int,
    ) -> List[str]:
        if not self.api_key:
            raise RuntimeError("FMP API key missing")
        params = {
            "priceMoreThan": str(price_more_than),
            "volumeMoreThan": str(volume_more_than),
            "marketCapMoreThan": str(market_cap_more_than),
            "limit": str(limit),
            "apikey": self.api_key,
        }
        url = "https://financialmodelingprep.com/api/v3/stock-screener"
        try:
            response = requests.get(url, params=params, timeout=self.timeout_s)
        except requests.RequestException as exc:
            raise RuntimeError(f"FMP screener request failed: {exc}") from exc
        if response.status_code == 429:
            raise RuntimeError("FMP screener rate limit hit")
        if not response.ok:
            raise RuntimeError(f"FMP screener error: {response.status_code} {response.text}")
        data = response.json()
        if not isinstance(data, list):
            raise RuntimeError("FMP screener returned invalid data")
        rows = data
        symbols: List[str] = []
        for row in rows:
            symbol = row.get("symbol")
            if isinstance(symbol, str):
                symbols.append(symbol)
        return symbols
