from __future__ import annotations

import os
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from bot.config import BotConfig
from bot.logger import BotLog
from bot.market import evaluate_market_gate
from bot.run_artifacts import write_run_artifacts
from bot.runner import Bot
from bot.trader import scan_market


class PaperAlpaca(Protocol):
    base_url: str

    def daily_bars(self, symbol: str, end_date: date, limit: int):
        raise NotImplementedError

    def symbol_metadata(self, symbol: str):
        raise NotImplementedError

    def account_equity(self) -> float:
        raise NotImplementedError

    def open_positions(self):
        raise NotImplementedError

    def assert_paper_trading(self) -> None:
        raise NotImplementedError

    def submit_order_with_response(self, symbol: str, qty: int, side: str):
        raise NotImplementedError


class PaperFmp(Protocol):
    def quarterly_fundamentals(self, symbol: str):
        raise NotImplementedError

    def annual_fundamentals(self, symbol: str):
        raise NotImplementedError

    def shares_outstanding(self, symbol: str):
        raise NotImplementedError

    def institutional_ownership(self, symbol: str):
        raise NotImplementedError

    def press_releases(self, symbol: str, limit: int, as_of: date):
        raise NotImplementedError

    def stock_screener(
        self,
        *,
        price_more_than: float,
        volume_more_than: int,
        market_cap_more_than: int,
        limit: int,
    ):
        raise NotImplementedError


class PaperLlm(Protocol):
    def classify_new(self, text: str) -> bool:
        raise NotImplementedError


def _serialize_candidates(candidates, limit: int) -> list[dict[str, object]]:
    top = candidates[:limit]
    return [
        {
            "symbol": candidate.symbol,
            "pivot": candidate.pivot,
            "close": candidate.close,
            "rs_percentile": candidate.rs_percentile,
        }
        for candidate in top
    ]


def _serialize_orders(orders) -> list[dict[str, object]]:
    return [asdict(order) for order in orders]


def _reason_counts(orders) -> dict[str, int]:
    counts: dict[str, int] = {}
    for order in orders:
        counts[order.reason] = counts.get(order.reason, 0) + 1
    return counts


def execute_paper(
    symbols: list[str],
    as_of: date,
    alpaca: PaperAlpaca,
    fmp: PaperFmp,
    llm: PaperLlm,
    config: BotConfig,
    confirm: bool,
    runs_dir: Path,
) -> int:
    log = BotLog()
    status = "ok"
    error_message = ""
    orders = []
    responses: list[dict[str, object]] = []
    positions_before = []
    positions_after = []
    market_state = "UNKNOWN"
    candidates = []
    market_data: dict[str, object] = {}

    try:
        positions_before = alpaca.open_positions()
        scan_result = scan_market(symbols, as_of, alpaca, fmp, llm, config, log)
        candidates = scan_result.candidates
        market_state = scan_result.market_state.value

        try:
            spy_bars = alpaca.daily_bars("SPY", end_date=as_of, limit=300)
            qqq_bars = alpaca.daily_bars("QQQ", end_date=as_of, limit=300)
            market_gate = evaluate_market_gate(spy_bars, qqq_bars, config, log)
            market_data = {
                "date": as_of.isoformat(),
                "market_state": market_gate.state.value,
                "dist_count": market_gate.distribution.count,
                "ftd_date": market_gate.ftd_date.isoformat() if market_gate.ftd_date else None,
                "rally_day_count": market_gate.rally_day_count,
            }
        except Exception as exc:
            log.error(f"Market report unavailable: {exc}")
            market_data = {
                "date": as_of.isoformat(),
                "market_state": None,
                "dist_count": None,
                "ftd_date": None,
                "rally_day_count": None,
            }

        bot = Bot(alpaca=alpaca, fmp=fmp, llm=llm, config=config)
        orders, run_log = bot.run_daily(symbols, as_of)
        log.entries.extend(run_log.entries)
    except Exception as exc:
        status = "error"
        error_message = str(exc)
        log.error(f"Execute-paper aborted: {exc}")

    print(f"Market state: {market_state}")
    print("Candidates:")
    for candidate in _serialize_candidates(candidates, config.max_positions):
        print(
            f"  {candidate['symbol']} pivot {candidate['pivot']:.2f} close {candidate['close']:.2f} "
            f"RS {candidate['rs_percentile']:.2f}"
        )
    print("Orders:")
    for order in orders:
        print(f"  {order.side.upper()} {order.qty} {order.symbol} ({order.reason})")

    orders_submitted: list[dict[str, object]] = []
    if status == "ok" and confirm:
        try:
            alpaca.assert_paper_trading()
            for order in orders:
                response = alpaca.submit_order_with_response(order.symbol, order.qty, order.side)
                responses.append(response)
                submitted = response.get("ok", False)
                orders_submitted.append({**asdict(order), "submitted": submitted})
                if not submitted:
                    status = "error"
                    error_message = response.get("error", "order submission failed")
                    log.error(f"Submit aborted: {error_message}")
                    break
        except Exception as exc:
            status = "error"
            error_message = str(exc)
            log.error(f"Submit aborted: {exc}")
    else:
        orders_submitted = [{**asdict(order), "submitted": False} for order in orders]

    if status == "ok" and confirm:
        try:
            positions_after = alpaca.open_positions()
        except Exception as exc:
            status = "error"
            error_message = str(exc)
            log.error(f"Positions snapshot failed: {exc}")
    else:
        positions_after = positions_before

    summary = {
        "status": status,
        "as_of": as_of.isoformat(),
        "market_state": market_state,
        "candidates": _serialize_candidates(candidates, config.max_positions),
        "orders_planned": _serialize_orders(orders),
        "confirm": confirm,
        "error": error_message,
        "reason_counts": _reason_counts(orders),
        "buy_count": sum(1 for order in orders if order.side == "buy"),
        "sell_count": sum(1 for order in orders if order.side == "sell"),
        "add_count": sum(1 for order in orders if order.reason == "can-slim-add"),
    }
    env_mode = {
        "alpaca_base_url": alpaca.base_url,
        "alpaca_paper_env": os.getenv("ALPACA_PAPER"),
        "paper_ack_env": os.getenv("REQUIRE_PAPER_ACK"),
    }
    write_run_artifacts(
        base_dir=runs_dir,
        run_date=as_of,
        timestamp=datetime.now(),
        summary=summary,
        log=log,
        orders_submitted=orders_submitted,
        orders_responses=responses,
        positions_before=positions_before,
        positions_after=positions_after,
        config=config,
        env_mode=env_mode,
        orders_csv=[
            {
                "date": as_of.isoformat(),
                "symbol": order.symbol,
                "side": order.side,
                "qty": order.qty,
                "reason": order.reason,
            }
            for order in orders
        ],
        candidates_csv=[
            {
                "date": as_of.isoformat(),
                "symbol": candidate.symbol,
                "rs_percentile": candidate.rs_percentile,
                "pivot": candidate.pivot,
                "close": candidate.close,
            }
            for candidate in candidates
        ],
        market=market_data,
    )

    if status != "ok":
        print(f"ERROR: {error_message}")
        return 1
    if not confirm:
        print("Dry run: no orders submitted.")
    return 0
