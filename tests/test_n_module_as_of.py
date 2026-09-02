from __future__ import annotations

from datetime import date
from typing import List

from bot.logger import BotLog
from bot.models import PressRelease
from bot.n_module import evaluate_n_module
from bot.providers.fmp_live import FmpLive


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


class StubFmpLive(FmpLive):
    """FmpLive with the HTTP call stubbed out, one list of rows per page."""

    def __init__(self, pages: List[List[dict]]) -> None:
        super().__init__(api_key="x", log=BotLog())
        self.pages = pages
        self.pages_requested: List[str] = []

    def _request(self, path: str, params):  # type: ignore[override]
        page = int(params["page"])
        self.pages_requested.append(params["page"])
        return self.pages[page] if page < len(self.pages) else []


def test_provider_pages_back_past_releases_newer_than_as_of() -> None:
    newer = [{"date": "2024-07-01 09:00:00", "title": f"Later {i}", "text": "."} for i in range(5)]
    older = [{"date": "2024-05-01 09:00:00", "title": "Earlier", "text": "."}]
    fmp = StubFmpLive([newer, older])
    releases = fmp.press_releases("AAA", limit=5, as_of=AS_OF)
    assert [r.published_date for r in releases] == [date(2024, 5, 1)]
    assert fmp.pages_requested == ["0", "1", "2"]  # stops at the first empty page
