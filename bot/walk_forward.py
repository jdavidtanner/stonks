from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from bot.config import BotConfig
from bot.data_providers import AlpacaClient, FmpClient, LlmClient
from bot.logger import BotLog
from bot.models import Bar, Order, Position
from bot.runner import Bot


@dataclass
class SimulationFill:
    day: date
    symbol: str
    side: str
    qty: int
    price: float
    reason: str


@dataclass
class CostAttribution:
    """Carver-style decomposition: where the return actually came from and went."""

    starting_equity: float
    gross_pnl: float
    commission: float
    slippage: float
    net_pnl: float
    net_return: float
    benchmark_return: Optional[float]
    excess_return: Optional[float]


@dataclass
class SimulationResult:
    final_equity: float
    max_drawdown: float
    num_buys: int
    num_sells: int
    win_rate: float
    equity_curve: List[Tuple[date, float]] = field(default_factory=list)
    orders_by_day: Dict[date, List[Order]] = field(default_factory=dict)
    fills: List[SimulationFill] = field(default_factory=list)
    attribution: Optional[CostAttribution] = None


@dataclass
class PendingOrder:
    order: Order
    signal_day: date
    signal_price: float


class WalkForwardRunner:
    def _iterate_days(self, start_date: date, end_date: date) -> Iterable[date]:
        current = start_date
        while current <= end_date:
            yield current
            current += timedelta(days=1)

    def _build_pending_orders(
        self, orders: List[Order], signal_day: date, alpaca: AlpacaClient
    ) -> List[PendingOrder]:
        pending_orders: List[PendingOrder] = []
        for order in orders:
            bars = alpaca.daily_bars(order.symbol, end_date=signal_day, limit=1)
            if not bars:
                continue
            pending_orders.append(
                PendingOrder(
                    order=order,
                    signal_day=signal_day,
                    signal_price=bars[-1].close,
                )
            )
        return pending_orders

    def run(
        self,
        symbols: List[str],
        start_date: date,
        end_date: date,
        alpaca: AlpacaClient,
        fmp: FmpClient,
        llm: LlmClient,
        config: BotConfig,
        output_dir: Optional[Path] = None,
    ) -> SimulationResult:
        log = BotLog()
        cash = config.account_equity
        positions: Dict[str, Position] = {}
        pending_orders: List[PendingOrder] = []
        equity_curve: List[Tuple[date, float]] = []
        orders_by_day: Dict[date, List[Order]] = {}
        fills: List[SimulationFill] = []
        buy_count = 0
        sell_count = 0
        wins = 0
        commission_paid = 0.0
        slippage_paid = 0.0

        all_symbols = list(dict.fromkeys(symbols + ["SPY", "QQQ"]))

        class SimulatedAlpaca(AlpacaClient):
            def __init__(self, current_day: date):
                self.current_day = current_day

            def daily_bars(self, symbol: str, end_date: date, limit: int) -> List[Bar]:
                return alpaca.daily_bars(symbol, end_date=end_date, limit=limit)

            def symbol_metadata(self, symbol: str):
                return alpaca.symbol_metadata(symbol)

            def account_equity(self) -> float:
                total = cash
                for position in positions.values():
                    bars = alpaca.daily_bars(
                        position.symbol, end_date=self.current_day, limit=1
                    )
                    if not bars:
                        continue
                    total += position.qty * bars[-1].close
                return total

            def open_positions(self) -> List[Tuple[str, int, float, date]]:
                return [
                    (
                        position.symbol,
                        position.qty,
                        position.entry_price,
                        position.entry_date,
                    )
                    for position in positions.values()
                ]

        warmup_day = start_date - timedelta(days=1)
        warmup_alpaca = SimulatedAlpaca(warmup_day)
        warmup_bot = Bot(alpaca=warmup_alpaca, fmp=fmp, llm=llm, config=config)
        warmup_orders, _ = warmup_bot.run_daily(all_symbols, warmup_day)
        pending_orders = self._build_pending_orders(warmup_orders, warmup_day, alpaca)

        for day in self._iterate_days(start_date, end_date):
            sim_alpaca = SimulatedAlpaca(day)

            filled_orders: List[Order] = []
            if pending_orders:
                for pending in pending_orders:
                    order = pending.order
                    if order.side == "buy":
                        fill_price = pending.signal_price * (1 + config.slippage_pct)
                        slippage = (fill_price - pending.signal_price) * order.qty
                        cost = fill_price * order.qty + config.commission_per_trade
                        if cost > cash:
                            continue
                        cash -= cost
                        commission_paid += config.commission_per_trade
                        slippage_paid += slippage
                        if order.symbol in positions:
                            existing = positions[order.symbol]
                            positions[order.symbol] = Position(
                                symbol=existing.symbol,
                                qty=existing.qty + order.qty,
                                entry_price=existing.entry_price,
                                entry_date=existing.entry_date,
                            )
                        else:
                            positions[order.symbol] = Position(
                                symbol=order.symbol,
                                qty=order.qty,
                                entry_price=fill_price,
                                entry_date=day,
                            )
                        buy_count += 1
                    elif order.side == "sell":
                        bars = alpaca.daily_bars(order.symbol, end_date=day, limit=1)
                        if not bars:
                            continue
                        reference_price = bars[-1].open
                        fill_price = reference_price * (1 - config.slippage_pct)
                        if order.symbol not in positions:
                            continue
                        position = positions[order.symbol]
                        slippage_paid += (reference_price - fill_price) * position.qty
                        cash += fill_price * position.qty - config.commission_per_trade
                        commission_paid += config.commission_per_trade
                        sell_count += 1
                        if fill_price > position.entry_price:
                            wins += 1
                        del positions[order.symbol]
                    filled_orders.append(order)
                    fills.append(
                        SimulationFill(
                            day=day,
                            symbol=order.symbol,
                            side=order.side,
                            qty=order.qty,
                            price=fill_price,
                            reason=order.reason,
                        )
                    )
                pending_orders = []

            bot = Bot(alpaca=sim_alpaca, fmp=fmp, llm=llm, config=config)
            orders, run_log = bot.run_daily(all_symbols, day)
            orders_by_day[day] = orders
            pending_orders = self._build_pending_orders(orders, day, alpaca)

            total_equity = cash
            for position in positions.values():
                bars = alpaca.daily_bars(position.symbol, end_date=day, limit=1)
                if not bars:
                    continue
                total_equity += position.qty * bars[-1].close
            equity_curve.append((day, total_equity))

        max_drawdown = 0.0
        peak = None
        for _day, equity in equity_curve:
            if peak is None or equity > peak:
                peak = equity
            if peak:
                drawdown = (peak - equity) / peak
                if drawdown > max_drawdown:
                    max_drawdown = drawdown

        win_rate = wins / sell_count if sell_count else 0.0
        final_equity = equity_curve[-1][1] if equity_curve else cash

        starting_equity = config.account_equity
        net_pnl = final_equity - starting_equity
        net_return = net_pnl / starting_equity if starting_equity else 0.0
        benchmark_return = self._benchmark_return(alpaca, start_date, end_date)
        attribution = CostAttribution(
            starting_equity=starting_equity,
            # gross is what the positions made before execution costs came out
            gross_pnl=net_pnl + commission_paid + slippage_paid,
            commission=commission_paid,
            slippage=slippage_paid,
            net_pnl=net_pnl,
            net_return=net_return,
            benchmark_return=benchmark_return,
            excess_return=(
                net_return - benchmark_return if benchmark_return is not None else None
            ),
        )

        result = SimulationResult(
            final_equity=final_equity,
            max_drawdown=max_drawdown,
            num_buys=buy_count,
            num_sells=sell_count,
            win_rate=win_rate,
            equity_curve=equity_curve,
            orders_by_day=orders_by_day,
            fills=fills,
            attribution=attribution,
        )
        if output_dir is not None:
            self._write_reports(output_dir, result)
        return result

    def _benchmark_return(
        self, alpaca: AlpacaClient, start_date: date, end_date: date
    ) -> Optional[float]:
        """SPY buy-and-hold over the same window. ponytail: SPY only, add a
        config benchmark symbol if another one is ever wanted."""
        first = alpaca.daily_bars("SPY", end_date=start_date, limit=1)
        last = alpaca.daily_bars("SPY", end_date=end_date, limit=1)
        if not first or not last or not first[-1].close:
            return None
        return (last[-1].close - first[-1].close) / first[-1].close

    def _write_reports(self, output_dir: Path, result: SimulationResult) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        equity_path = output_dir / "equity_curve.csv"
        trades_path = output_dir / "trades.csv"
        try:
            with equity_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["date", "equity"])
                writer.writeheader()
                for day, equity in result.equity_curve:
                    writer.writerow({"date": day.isoformat(), "equity": f"{equity:.2f}"})
        except Exception:
            pass
        if result.attribution is not None:
            self._write_attribution(output_dir, result.attribution)
        try:
            with trades_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["date", "symbol", "side", "qty", "price", "reason"]
                )
                writer.writeheader()
                for fill in result.fills:
                    writer.writerow(
                        {
                            "date": fill.day.isoformat(),
                            "symbol": fill.symbol,
                            "side": fill.side,
                            "qty": fill.qty,
                            "price": f"{fill.price:.2f}",
                            "reason": fill.reason,
                        }
                    )
        except Exception:
            pass

    def _write_attribution(self, output_dir: Path, attribution: CostAttribution) -> None:
        rows = [
            ("starting_equity", attribution.starting_equity),
            ("gross_pnl", attribution.gross_pnl),
            ("commission", -attribution.commission),
            ("slippage", -attribution.slippage),
            ("net_pnl", attribution.net_pnl),
            ("net_return", attribution.net_return),
            ("benchmark_return", attribution.benchmark_return),
            ("excess_return", attribution.excess_return),
        ]
        try:
            with (output_dir / "attribution.csv").open(
                "w", encoding="utf-8", newline=""
            ) as handle:
                writer = csv.writer(handle)
                writer.writerow(["component", "value"])
                for name, value in rows:
                    writer.writerow([name, "" if value is None else f"{value:.4f}"])
        except Exception:
            pass
