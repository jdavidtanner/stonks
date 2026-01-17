from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional


from bot.data_providers import LlmClient
from bot.logger import BotLog


@dataclass
class OpenAiNClassifier(LlmClient):
    api_key: str
    model: str
    log: BotLog
    timeout_s: float = 10.0

    def _parse_response(self, content: str) -> bool:
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            self.log.warn("LLM response parse failed; returning has_new=False.")
            return False
        has_new = data.get("has_new")
        if isinstance(has_new, bool):
            return has_new
        self.log.warn("LLM response missing has_new; returning has_new=False.")
        return False

    def classify_new(self, text: str) -> bool:
        if not self.api_key or not self.model:
            self.log.warn("LLM credentials missing; returning has_new=False.")
            return False
        try:
            import requests
        except ModuleNotFoundError:
            self.log.warn("requests not available; returning has_new=False.")
            return False
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a strict classifier. Respond ONLY with JSON like "
                        "{\"has_new\": true/false, \"category\": \"...\", \"confidence\": 0.0}."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Classify whether this text describes a NEW product, service, or catalyst:\n{text}",
                },
            ],
            "temperature": 0,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        for attempt in range(2):
            try:
                response = requests.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_s,
                )
            except requests.RequestException as exc:
                self.log.warn(f"LLM request failed: {exc}")
                continue
            if response.status_code == 429:
                self.log.warn("LLM rate limit hit; returning has_new=False.")
                return False
            if response.ok:
                data = response.json()
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                return self._parse_response(content)
            self.log.warn(f"LLM API error: {response.status_code} {response.text}")
        return False
