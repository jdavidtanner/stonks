from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, List

from bot.config import BotConfig
from bot.data_providers import AlpacaClient, FmpClient, LlmClient
from bot.logger import BotLog
from bot.models import Order, Position
from bot.sell_rules import HOLD_DISABLE_DAYS, evaluate_sell_signals
from bot.trader import build_add_on_orders, build_orders, check_stops, scan_market


@dataclass
class Bot:
    alpaca: AlpacaClient
    fmp: FmpClient
    llm: LlmClient
    config: BotConfig

    def run_daily(self, symbols: Iterable[str], end_date: date) -> tuple[List[Order], BotLog]:
        log = BotLog()
        scan = scan_market(symbols, end_date, self.alpaca, self.fmp, self.llm, self.config, log)
        positions = [
            Position(symbol=symbol, qty=qty, entry_price=entry_price, entry_date=entry_date)
            for symbol, qty, entry_price, entry_date in self.alpaca.open_positions()
        ]
        account_equity = self.alpaca.account_equity()
        latest_prices = {
            symbol: bars[-1].close
            for symbol in symbols
            if (bars := self.alpaca.daily_bars(symbol, end_date=end_date, limit=1))
        }
        stop_orders = check_stops(positions, latest_prices, self.config, log)
        stop_symbols = {order.symbol for order in stop_orders}

        history_limit = max(60, HOLD_DISABLE_DAYS + 5)
        price_history = {
            position.symbol: self.alpaca.daily_bars(
                position.symbol, end_date=end_date, limit=history_limit
            )
            for position in positions
        }
        sell_positions = [position for position in positions if position.symbol not in stop_symbols]
        sell_orders = evaluate_sell_signals(
            sell_positions,
            price_history,
            end_date,
            log,
        )
        sell_symbols = {order.symbol for order in sell_orders}
        add_state = {}
        if hasattr(self.alpaca, "add_state_snapshot"):
            add_state = self.alpaca.add_state_snapshot([position.symbol for position in positions])
        add_orders = build_add_on_orders(
            scan,
            [
                position
                for position in positions
                if position.symbol not in stop_symbols | sell_symbols
            ],
            latest_prices,
            account_equity,
            self.config,
            log,
            add_state,
        )
        if hasattr(self.alpaca, "record_add"):
            for order in add_orders:
                add_price = latest_prices.get(order.symbol)
                if add_price is not None:
                    self.alpaca.record_add(order.symbol, add_price)
        entry_orders = build_orders(scan, positions, account_equity, self.config, log)
        filtered_entry_orders = [
            order for order in entry_orders if order.symbol not in sell_symbols
        ]
        orders = stop_orders + sell_orders + add_orders + filtered_entry_orders
        return orders, log
