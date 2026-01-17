from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from bot.logger import BotLog


@dataclass
class PositionState:
    entry_date: date
    add_count: int = 0
    last_add_price: Optional[float] = None


@dataclass
class PositionStateStore:
    path: Path

    def _load_raw(self) -> Dict[str, object]:
        if not self.path.exists():
            return {}
        with self.path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return {symbol: entry for symbol, entry in data.items()}

    def _save_raw(self, data: Dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)

    def _load_state(self) -> Dict[str, PositionState]:
        raw = self._load_raw()
        states: Dict[str, PositionState] = {}
        for symbol, entry in raw.items():
            if isinstance(entry, str):
                try:
                    entry_date = date.fromisoformat(entry)
                except ValueError:
                    continue
                states[symbol] = PositionState(entry_date=entry_date)
                continue
            if isinstance(entry, dict):
                entry_date_str = entry.get("entry_date")
                if not isinstance(entry_date_str, str):
                    continue
                try:
                    entry_date = date.fromisoformat(entry_date_str)
                except ValueError:
                    continue
                add_count = entry.get("add_count", 0)
                last_add_price = entry.get("last_add_price")
                if not isinstance(add_count, int):
                    add_count = 0
                if last_add_price is not None and not isinstance(last_add_price, (int, float)):
                    last_add_price = None
                states[symbol] = PositionState(
                    entry_date=entry_date,
                    add_count=add_count,
                    last_add_price=float(last_add_price) if last_add_price is not None else None,
                )
        return states

    def _save_state(self, states: Dict[str, PositionState]) -> None:
        payload: Dict[str, object] = {}
        for symbol, state in states.items():
            payload[symbol] = {
                "entry_date": state.entry_date.isoformat(),
                "add_count": state.add_count,
                "last_add_price": state.last_add_price,
            }
        self._save_raw(payload)

    def record_entry(self, symbol: str, entry_date: date) -> None:
        states = self._load_state()
        states[symbol] = PositionState(entry_date=entry_date)
        self._save_state(states)

    def remove_entry(self, symbol: str) -> None:
        states = self._load_state()
        if symbol in states:
            del states[symbol]
            self._save_state(states)

    def record_add(self, symbol: str, add_price: float) -> None:
        states = self._load_state()
        state = states.get(symbol)
        if state is None:
            return
        state.add_count += 1
        state.last_add_price = add_price
        self._save_state(states)

    def add_state_snapshot(
        self, symbols: Iterable[str]
    ) -> Dict[str, Tuple[int, Optional[float]]]:
        states = self._load_state()
        snapshot: Dict[str, Tuple[int, Optional[float]]] = {}
        for symbol in symbols:
            state = states.get(symbol)
            if state is None:
                snapshot[symbol] = (0, None)
            else:
                snapshot[symbol] = (state.add_count, state.last_add_price)
        return snapshot

    def reconcile_positions(
        self,
        positions: List[Tuple[str, int, float]],
        as_of: date,
        log: BotLog,
    ) -> List[Tuple[str, int, float, date]]:
        states = self._load_state()
        symbols = {symbol for symbol, _, _ in positions}
        for symbol in list(states.keys()):
            if symbol not in symbols:
                del states[symbol]
        reconciled: List[Tuple[str, int, float, date]] = []
        for symbol, qty, entry_price in positions:
            state = states.get(symbol)
            if state is not None:
                entry_date = state.entry_date
            else:
                entry_date = as_of - timedelta(days=1)
                log.warn(
                    f"{symbol} missing entry_date in state; using {entry_date.isoformat()}."
                )
                state = PositionState(entry_date=entry_date)
                states[symbol] = state
            reconciled.append((symbol, qty, entry_price, entry_date))
        self._save_state(states)
        return reconciled
