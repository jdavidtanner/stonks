from __future__ import annotations

from bot.logger import BotLog
from bot.providers.llm_claude import ClaudeNClassifier


def _classifier() -> ClaudeNClassifier:
    return ClaudeNClassifier(api_key="key", model="claude-sonnet-4-6", log=BotLog())


def test_parse_valid_json_true() -> None:
    c = _classifier()
    assert c._parse_response('{"has_new": true, "category": "product", "confidence": 0.9}')


def test_parse_valid_json_false() -> None:
    c = _classifier()
    assert not c._parse_response('{"has_new": false, "category": "none", "confidence": 0.1}')


def test_parse_invalid_json_fails_closed() -> None:
    c = _classifier()
    assert not c._parse_response("not-json")
    assert any("parse failed" in entry.lower() for entry in c.log.entries)


def test_parse_missing_has_new_fails_closed() -> None:
    c = _classifier()
    assert not c._parse_response('{"category": "product"}')
    assert any("missing has_new" in entry.lower() for entry in c.log.entries)


def test_parse_none_fails_closed() -> None:
    c = _classifier()
    assert not c._parse_response(None)


def test_missing_credentials_fails_closed() -> None:
    c = ClaudeNClassifier(api_key="", model="", log=BotLog())
    assert not c.classify_new("some text")
    assert any("credentials missing" in entry.lower() for entry in c.log.entries)
