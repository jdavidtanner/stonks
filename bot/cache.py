from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Tuple


@dataclass
class RunCache:
    store: Dict[Tuple[Any, ...], Any] = field(default_factory=dict)

    def get_or_set(self, key: Tuple[Any, ...], factory: Callable[[], Any]) -> Any:
        if key in self.store:
            return self.store[key]
        value = factory()
        self.store[key] = value
        return value
