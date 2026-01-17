from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class BotLog:
    entries: List[str] = field(default_factory=list)

    def info(self, message: str) -> None:
        self.entries.append(message)

    def warn(self, message: str) -> None:
        self.entries.append(f"WARNING: {message}")

    def error(self, message: str) -> None:
        self.entries.append(f"ERROR: {message}")
