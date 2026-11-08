from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from bot.config import BotConfig
from bot.logger import BotLog


def _serialize_date(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _serialize_positions(positions: Iterable[tuple[str, int, float, date]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for symbol, qty, entry_price, entry_date in positions:
        results.append(
            {
                "symbol": symbol,
                "qty": qty,
                "entry_price": entry_price,
                "entry_date": _serialize_date(entry_date),
            }
        )
    return results


def _write_csv(
    path: Path,
    rows: Iterable[dict[str, Any]],
    fieldnames: list[str],
    log: BotLog,
) -> None:
    try:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
    except Exception as exc:
        log.error(f"Failed to write {path.name}: {exc}")


def write_run_artifacts(
    base_dir: Path,
    run_date: date,
    timestamp: datetime,
    summary: dict[str, Any],
    log: BotLog,
    orders_submitted: list[dict[str, Any]],
    orders_responses: list[dict[str, Any]],
    positions_before: Iterable[tuple[str, int, float, date]],
    positions_after: Iterable[tuple[str, int, float, date]],
    config: BotConfig,
    env_mode: dict[str, Any],
    orders_csv: list[dict[str, Any]],
    candidates_csv: list[dict[str, Any]],
    market: dict[str, Any],
) -> Path:
    date_dir = base_dir / run_date.isoformat()
    run_dir = date_dir / f"run_{timestamp.strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)

    try:
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    except Exception as exc:
        log.error(f"Failed to write summary.json: {exc}")
    try:
        (run_dir / "log.txt").write_text("\n".join(log.entries), encoding="utf-8")
    except Exception as exc:
        log.error(f"Failed to write log.txt: {exc}")
    try:
        (run_dir / "orders_submitted.json").write_text(
            json.dumps(orders_submitted, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        log.error(f"Failed to write orders_submitted.json: {exc}")
    try:
        (run_dir / "orders_responses.json").write_text(
            json.dumps(orders_responses, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        log.error(f"Failed to write orders_responses.json: {exc}")
    try:
        (run_dir / "positions_before.json").write_text(
            json.dumps(_serialize_positions(positions_before), indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        log.error(f"Failed to write positions_before.json: {exc}")
    try:
        (run_dir / "positions_after.json").write_text(
            json.dumps(_serialize_positions(positions_after), indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        log.error(f"Failed to write positions_after.json: {exc}")
    try:
        (run_dir / "config.json").write_text(
            json.dumps({"config": asdict(config), "env_mode": env_mode}, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        log.error(f"Failed to write config.json: {exc}")
    try:
        (run_dir / "market.json").write_text(json.dumps(market, indent=2), encoding="utf-8")
    except Exception as exc:
        log.error(f"Failed to write market.json: {exc}")
    _write_csv(
        run_dir / "orders.csv",
        orders_csv,
        ["date", "symbol", "side", "qty", "reason"],
        log,
    )
    _write_csv(
        run_dir / "candidates.csv",
        candidates_csv,
        ["date", "symbol", "rs_percentile", "pivot", "close"],
        log,
    )
    return run_dir
