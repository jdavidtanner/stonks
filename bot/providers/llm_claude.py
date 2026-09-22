from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Optional

from bot.data_providers import LlmClient
from bot.logger import BotLog


@dataclass
class ClaudeNClassifier(LlmClient):
    api_key: str
    model: str
    log: BotLog
    timeout_s: float = 15.0

    def _call(self, content: list) -> Optional[str]:
        if not self.api_key or not self.model:
            self.log.warn("Claude credentials missing.")
            return None
        try:
            import anthropic
        except ModuleNotFoundError:
            self.log.warn("anthropic package not available.")
            return None
        client = anthropic.Anthropic(api_key=self.api_key)
        try:
            message = client.messages.create(
                model=self.model,
                max_tokens=256,
                system=(
                    "You are a strict classifier. Respond ONLY with JSON like "
                    '{"has_new": true/false, "category": "...", "confidence": 0.0}.'
                ),
                messages=[{"role": "user", "content": content}],
            )
            return message.content[0].text if message.content else None
        except Exception as exc:
            self.log.warn(f"Claude API error: {exc}")
            return None

    def _parse_response(self, raw: Optional[str]) -> bool:
        if raw is None:
            return False
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self.log.warn("Claude response parse failed; returning has_new=False.")
            return False
        has_new = data.get("has_new")
        if isinstance(has_new, bool):
            return has_new
        self.log.warn("Claude response missing has_new; returning has_new=False.")
        return False

    def classify_new(self, text: str) -> bool:
        content = [
            {
                "type": "text",
                "text": f"Classify whether this text describes a NEW product, service, or catalyst:\n{text}",
            }
        ]
        return self._parse_response(self._call(content))

    def classify_video(self, video_data: bytes, media_type: str = "video/mp4") -> bool:
        """Classify a stock chart video for new catalysts or breakout signals."""
        encoded = base64.standard_b64encode(video_data).decode("utf-8")
        content = [
            {
                "type": "video",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": encoded,
                },
            },
            {
                "type": "text",
                "text": (
                    "Analyze this stock chart video. "
                    "Classify whether it shows a NEW breakout, catalyst, or significant bullish signal. "
                    "Respond ONLY with JSON: "
                    '{"has_new": true/false, "category": "...", "confidence": 0.0}'
                ),
            },
        ]
        return self._parse_response(self._call(content))
