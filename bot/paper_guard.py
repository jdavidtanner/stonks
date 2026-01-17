from __future__ import annotations

import os


def require_paper_trading(base_url: str) -> None:
    requires_ack = os.getenv("REQUIRE_PAPER_ACK") == "I_UNDERSTAND_THIS_IS_PAPER"
    is_paper_env = os.getenv("ALPACA_PAPER") == "1"
    is_paper_url = "paper" in base_url.lower()
    if not (is_paper_url or is_paper_env):
        raise RuntimeError("Alpaca base URL is not paper; refusing to trade.")
    if not requires_ack:
        raise RuntimeError("Missing REQUIRE_PAPER_ACK acknowledgment; refusing to trade.")
