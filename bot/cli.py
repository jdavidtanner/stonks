from __future__ import annotations

import argparse
import os
from datetime import date, timedelta
from pathlib import Path

from bot.config import BotConfig
from bot.logger import BotLog
from bot.execute_paper import execute_paper
from bot.providers.alpaca_live import AlpacaLive
from bot.providers.fmp_live import FmpLive
from bot.providers.llm_openai import OpenAiNClassifier
from bot.providers.llm_claude import ClaudeNClassifier
from bot.runner import Bot
from bot.state_store import PositionStateStore


def _load_symbols(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Symbols file not found: {path}")
    symbols = []
    for line in path.read_text(encoding="utf-8").splitlines():
        symbol = line.strip().upper()
        if symbol:
            symbols.append(symbol)
    return symbols


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Run CAN SLIM bot daily scan.")
    parser.add_argument("--symbols", required=True, help="Path to newline-delimited symbols list.")
    parser.add_argument("--end-date", required=False, help="YYYY-MM-DD for scan date.")
    parser.add_argument(
        "--llm-provider",
        choices=["openai", "claude"],
        default="openai",
        help="LLM provider for N-module classification (default: openai).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview orders without execution.")
    parser.add_argument("--execute", action="store_true", help="Submit orders to Alpaca.")
    parser.add_argument("--walk-forward", action="store_true", help="Run walk-forward simulation.")
    parser.add_argument("--execute-paper", action="store_true", help="Run paper trading scan.")
    parser.add_argument("--confirm", action="store_true", help="Submit paper orders.")
    parser.add_argument("--as-of", required=False, help="YYYY-MM-DD for execute-paper date.")
    parser.add_argument("--start", required=False, help="YYYY-MM-DD for simulation start.")
    parser.add_argument("--end", required=False, help="YYYY-MM-DD for simulation end.")
    args = parser.parse_args()

    log = BotLog()
    try:
        alpaca_key = _require_env("ALPACA_KEY_ID")
        alpaca_secret = _require_env("ALPACA_SECRET_KEY")
        alpaca_base = _require_env("ALPACA_BASE_URL")
        fmp_key = _require_env("FMP_API_KEY")
        if args.llm_provider == "claude":
            llm_key = _require_env("ANTHROPIC_API_KEY")
            llm_model = _require_env("ANTHROPIC_MODEL")
        else:
            llm_key = _require_env("OPENAI_API_KEY")
            llm_model = _require_env("OPENAI_MODEL")
    except RuntimeError as exc:
        print(str(exc))
        return 1

    end_date = date.fromisoformat(args.end_date) if args.end_date else date.today()
    symbols = _load_symbols(Path(args.symbols))
    state_store = PositionStateStore(Path("state/positions.json"))

    if args.execute_paper and (args.execute or args.walk_forward):
        print("--execute-paper cannot be combined with --execute or --walk-forward.")
        return 1

    alpaca = AlpacaLive(
        key_id=alpaca_key,
        secret_key=alpaca_secret,
        base_url=alpaca_base,
        log=log,
        state_store=state_store,
        fail_closed=args.execute_paper,
    )
    fmp = FmpLive(api_key=fmp_key, log=log, fail_closed=args.execute_paper)
    if args.llm_provider == "claude":
        llm = ClaudeNClassifier(api_key=llm_key, model=llm_model, log=log)
    else:
        llm = OpenAiNClassifier(api_key=llm_key, model=llm_model, log=log)
    bot = Bot(alpaca=alpaca, fmp=fmp, llm=llm, config=BotConfig())

    if args.execute_paper:
        as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
        return execute_paper(
            symbols=symbols,
            as_of=as_of,
            alpaca=alpaca,
            fmp=fmp,
            llm=llm,
            config=BotConfig(),
            confirm=args.confirm,
            runs_dir=Path("runs"),
        )

    if args.walk_forward:
        if not args.start or not args.end:
            print("Walk-forward requires --start and --end.")
            return 1
        from bot.walk_forward import WalkForwardRunner

        start_date = date.fromisoformat(args.start)
        end_date = date.fromisoformat(args.end)
        runner = WalkForwardRunner()
        output_dir = Path("runs") / f"walk_forward_{date.today().isoformat()}"
        result = runner.run(
            symbols,
            start_date,
            end_date,
            alpaca,
            fmp,
            llm,
            BotConfig(),
            output_dir=output_dir,
        )
        print(f"Final equity: {result.final_equity:.2f}")
        print(f"Max drawdown: {result.max_drawdown:.2%}")
        print(f"Buys: {result.num_buys} Sells: {result.num_sells} Win rate: {result.win_rate:.2%}")
        print("Equity curve:")
        for day, equity in result.equity_curve:
            print(f"{day.isoformat()} {equity:.2f}")
        return 0

    orders, run_log = bot.run_daily(symbols, end_date)

    print("Orders:")
    for order in orders:
        print(f"  {order.side.upper()} {order.qty} {order.symbol} ({order.reason})")
    print("Log:")
    for entry in run_log.entries:
        print(f"  {entry}")

    if args.dry_run or not args.execute:
        print("Dry run: no orders submitted.")
        return 0

    for order in orders:
        if order.side == "buy":
            if alpaca.submit_order(order.symbol, order.qty, "buy"):
                state_store.record_entry(order.symbol, end_date + timedelta(days=1))
        elif order.side == "sell":
            if alpaca.submit_order(order.symbol, order.qty, "sell"):
                state_store.remove_entry(order.symbol)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
