from __future__ import annotations

from datetime import date
from typing import List

from bot.logger import BotLog
from bot.models import PressRelease
from bot.n_module import evaluate_n_module


class FakeFmp:
    """Returns everything it holds, ignoring as_of, so the clamp must happen in evaluate_n_module."""

    def __init__(self, releases: List[PressRelease]) -> None:
        self.releases = releases
        self.as_of_seen: List[date] = []

    def press_releases(self, symbol: str, limit: int, as_of: date) -> List[PressRelease]:
        self.as_of_seen.append(as_of)
        return self.releases[:limit]


class AlwaysNewLlm:
    def __init__(self) -> None:
        self.seen: List[str] = []

    def classify_new(self, text: str) -> bool:
        self.seen.append(text)
        return True


AS_OF = date(2024, 6, 1)


def test_release_after_as_of_is_excluded() -> None:
    fmp = FakeFmp([PressRelease(published_date=date(2024, 6, 2), text="Future launch.")])
    llm = AlwaysNewLlm()
    result = evaluate_n_module("AAA", AS_OF, fmp, llm, BotLog())
    assert not result.has_new
    assert llm.seen == []
    assert fmp.as_of_seen == [AS_OF]


def test_undated_release_fails_closed() -> None:
    fmp = FakeFmp([PressRelease(published_date=None, text="Undated launch.")])
    llm = AlwaysNewLlm()
    assert not evaluate_n_module("AAA", AS_OF, fmp, llm, BotLog()).has_new
    assert llm.seen == []


def test_release_on_or_before_as_of_is_used() -> None:
    fmp = FakeFmp(
        [
            PressRelease(published_date=date(2024, 6, 2), text="Future launch."),
            PressRelease(published_date=AS_OF, text="Same-day launch."),
        ]
    )
    llm = AlwaysNewLlm()
    assert evaluate_n_module("AAA", AS_OF, fmp, llm, BotLog()).has_new
    assert llm.seen == ["Same-day launch."]
