from __future__ import annotations

from bot.logger import BotLog
from bot.providers.llm_openai import OpenAiNClassifier


def test_llm_parse_valid_json() -> None:
    log = BotLog()
    classifier = OpenAiNClassifier(api_key="key", model="model", log=log)
    assert classifier._parse_response('{"has_new": true, "category": "product", "confidence": 0.9}')


def test_llm_parse_invalid_json_fails_closed() -> None:
    log = BotLog()
    classifier = OpenAiNClassifier(api_key="key", model="model", log=log)
    assert not classifier._parse_response("not-json")
    assert any("parse failed" in entry.lower() for entry in log.entries)


def test_llm_parse_missing_has_new_fails_closed() -> None:
    log = BotLog()
    classifier = OpenAiNClassifier(api_key="key", model="model", log=log)
    assert not classifier._parse_response('{"category": "product"}')
    assert any("missing has_new" in entry.lower() for entry in log.entries)
