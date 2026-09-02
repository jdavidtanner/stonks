from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional, Tuple

import requests

from bot.data_providers import AlpacaClient
from bot.logger import BotLog
from bot.models import Bar, SymbolMetadata
from bot.paper_guard import require_paper_trading
from bot.state_store import PositionStateStore


@dataclass
class AlpacaLive(AlpacaClient):
    key_id: str
    secret_key: str
    base_url: str
    log: BotLog
    state_store: PositionStateStore
    timeout_s: float = 10.0
    fail_closed: bool = False

    def assert_paper_trading(self) -> None:
        require_paper_trading(self.base_url)

    def _headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret_key,
        }

    def _request(self, method: str, path: str, params: Optional[Dict[str, str]] = None) -> Dict:
        url = f"{self.base_url.rstrip('/')}{path}"
        for attempt in range(2):
            try:
                response = requests.request(
                    method,
                    url,
                    headers=self._headers(),
                    params=params,
                    timeout=self.timeout_s,
                )
            except requests.RequestException as exc:
                message = f"Alpaca request failed: {exc}"
                if self.fail_closed:
                    self.log.error(message)
                    raise RuntimeError(message)
                self.log.warn(message)
                continue
            if response.status_code == 429:
                message = "Alpaca rate limit hit; failing closed."
                if self.fail_closed:
                    self.log.error(message)
                    raise RuntimeError(message)
                self.log.warn(message)
                raise RuntimeError("rate limit")
            if response.ok:
                return response.json()
            message = f"Alpaca API error: {response.status_code} {response.text}"
            if self.fail_closed:
                self.log.error(message)
                raise RuntimeError(message)
            self.log.warn(message)
        raise RuntimeError("Alpaca request failed")

    def daily_bars(self, symbol: str, end_date: date, limit: int) -> List[Bar]:
        params = {
            "timeframe": "1Day",
            "limit": str(limit),
            "end": datetime.combine(end_date, datetime.min.time()).isoformat() + "Z",
        }
        try:
            data = self._request("GET", f"/v2/stocks/{symbol}/bars", params=params)
        except RuntimeError:
            if self.fail_closed:
                raise
            return []
        bars = []
        for item in data.get("bars", []):
            try:
                day = datetime.fromisoformat(item["t"].replace("Z", "+00:00")).date()
                bars.append(
                    Bar(
                        day=day,
                        open=float(item["o"]),
                        high=float(item["h"]),
                        low=float(item["l"]),
                        close=float(item["c"]),
                        volume=int(item["v"]),
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue
        return bars

    def symbol_metadata(self, symbol: str) -> Optional[SymbolMetadata]:
        try:
            data = self._request("GET", f"/v2/assets/{symbol}")
        except RuntimeError:
            if self.fail_closed:
                raise
            return None
        asset_class = data.get("class") or data.get("asset_class")
        exchange = data.get("exchange")
        tradable = data.get("tradable")
        is_us_common_stock = (
            asset_class == "us_equity"
            and tradable is True
            and exchange in {"NYSE", "NASDAQ", "AMEX"}
        )
        if is_us_common_stock:
            return SymbolMetadata(symbol=symbol, is_us_common_stock=True, exchange=exchange)
        return None

    def account_equity(self) -> float:
        try:
            data = self._request("GET", "/v2/account")
        except RuntimeError:
            if self.fail_closed:
                raise
            return 0.0
        equity = data.get("equity")
        try:
            return float(equity)
        except (TypeError, ValueError):
            return 0.0

    def open_positions(self) -> List[Tuple[str, int, float, date]]:
        try:
            data = self._request("GET", "/v2/positions")
        except RuntimeError:
            if self.fail_closed:
                raise
            return []
        positions: List[Tuple[str, int, float]] = []
        for item in data:
            try:
                symbol = item["symbol"]
                qty = int(float(item["qty"]))
                entry_price = float(item["avg_entry_price"])
            except (KeyError, ValueError, TypeError):
                continue
            positions.append((symbol, qty, entry_price))
        return self.state_store.reconcile_positions(positions, date.today(), self.log)

    def submit_order(self, symbol: str, qty: int, side: str) -> bool:
        self.assert_paper_trading()
        params = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "market",
            "time_in_force": "day",
        }
        try:
            self._request("POST", "/v2/orders", params=params)
        except RuntimeError:
            return False
        return True

    def add_state_snapshot(
        self, symbols: Iterable[str]
    ) -> Dict[str, Tuple[int, Optional[float]]]:
        return self.state_store.add_state_snapshot(symbols)

    def record_add(self, symbol: str, add_price: float) -> None:
        self.state_store.record_add(symbol, add_price)

    def submit_order_with_response(self, symbol: str, qty: int, side: str) -> Dict[str, object]:
        self.assert_paper_trading()
        params = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "market",
            "time_in_force": "day",
        }
        try:
            data = self._request("POST", "/v2/orders", params=params)
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "response": data}
