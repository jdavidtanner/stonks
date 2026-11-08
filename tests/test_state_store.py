from __future__ import annotations

from datetime import date
from pathlib import Path

from bot.logger import BotLog
from bot.state_store import PositionStateStore


def test_state_store_reconciles_missing_entry_date(tmp_path: Path) -> None:
    store = PositionStateStore(tmp_path / "positions.json")
    log = BotLog()
    reconciled = store.reconcile_positions(
        positions=[("AAA", 10, 100.0)],
        as_of=date(2024, 6, 1),
        log=log,
    )
    assert reconciled[0][0] == "AAA"
    assert reconciled[0][3] == date(2024, 5, 31)
    assert any("missing entry_date" in entry.lower() for entry in log.entries)
