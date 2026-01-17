# CAN SLIM Canon (v1.0-book-faithful)

This document defines the exact CAN SLIM interpretation implemented in this repo. It is the authoritative reference for the strategy behavior and **must** be updated if any of these rules or defaults change.

## 1) Purpose + operating mode

- **Timeframe:** Daily bars only. Intraday data is not used.
- **Workflow:** Scan after the close; act at the next open (paper/live submit next session). Walk-forward uses the same sequencing with next-day fills.
- **Fail-closed:** Missing or insufficient data rejects a symbol. Provider errors in execute-paper fail closed and abort safely.

## 2) Exact pipeline (in order)

### 2.1 Market gate (follow-through + distribution)
- Requires sufficient index data; otherwise market is OFF.
- Computes distribution day count on SPY/QQQ over `distribution_window` and turns market OFF if it reaches `distribution_off`, with warnings at `distribution_warning`.
- Requires a follow-through day (FTD) between `ftd_min_day` and `ftd_max_day` after a rally attempt, with gain ≥ `ftd_min_gain` and higher volume than the prior day.

### 2.2 Universe prefilter
- If FMP screener is available, uses it with:
  - `price_more_than = BotConfig.min_price`
  - `volume_more_than = 500_000`
  - `market_cap_more_than = 300_000_000`
  - `limit = 1000`
- If unavailable or errors, falls back to the input symbols list.

### 2.3 Universe filters
For each symbol, the scan enforces:
- US common stock metadata only.
- Minimum history (`min_history_days`) of daily bars.
- Minimum price (`min_price`).
- Minimum average dollar volume over the last 50 days (`min_avg_dollar_volume`).

### 2.4 Relative Strength (RS)
- Computes 250-day total return for each candidate in the filtered universe.
- Converts returns into percentiles; requires `rs_min_percentile` or higher.

### 2.5 C/A fundamentals gate (quarterly + annual)
All fundamentals use **eligible data only** (`accepted_date <= as_of`). Missing data fails closed.

**Quarterly checks:**
- Latest quarter EPS YoY growth ≥ 20%.
- Latest quarter revenue YoY growth must be positive.
- **EPS distortion guard:** If EPS YoY growth > 200% and revenue YoY growth < 20%, reject.
- **Acceleration requirement (PR2):**
  - Use the latest 3 eligible quarters (Q0, Q1, Q2) and their year-ago counterparts.
  - Compute `g0`, `g1`, `g2` (EPS YoY growth for each).
  - Require `g0 >= g1 >= g2` and `g0 >= 0.20`. Otherwise reject.

**Annual checks:**
- Minimum 3 years of annual EPS history.
- Annual EPS must be positive and increasing across the span.
- Annual revenue must be positive and increasing across the span.
- **Net income margin guard:** If net income data is present for the latest two years, reject if the latest margin falls below 50% of the prior margin or if any margin is non-positive.

### 2.6 S supply/demand gate (sponsorship)
Uses eligible snapshots only (`accepted_date <= as_of`).
- Shares outstanding must be present and > 0.
- Institutional ownership must have **at least two eligible snapshots** and both > 0.
- **Non-decline requirement (PR2):** latest institutional owners must be >= previous; otherwise reject.

### 2.7 N module (new catalyst)
- Pulls up to 5 press releases from FMP.
- An OpenAI classifier returns JSON; non-JSON or missing `has_new` fails closed to `False`.
- Any release classified as new sets `has_new=True`; otherwise reject.

### 2.8 Pattern gate (flat base)
Flat base detection is the only pattern used:
- Requires **at least 76 bars** of history.
- Searches windows of 25–40 days ending **the day before the breakout**.
- Depth constraint: max-high to min-low ≤ 15%.
- Volume dry-up: base avg volume must be ≤ 90% of the 50-day avg.
- Tightness: close range within base ≤ 8%.
- Breakout day must close above the pivot (base max high) with volume ≥ 1.5x the 50-day avg.
- Bases must be adjacent to the breakout day; old bases are rejected.
- Buy range enforcement: breakout close must be within `max_pivot_buy_pct` (5%) above pivot.

### 2.9 Portfolio + execution rules
- **Max positions:** `max_positions` (equal allocation per slot).
- **Entry sizing:** `allocation = account_equity / max_positions`, `qty = floor(allocation / close)`.
- **Stop loss:** sell if price falls ≥ `stop_loss_pct` (8%) from entry.

**Sell rules (in priority order after stops):**
- SMA50 violation (close below 50-day SMA).
- Climax top (large range/volume reversal after ≥ 30% gain).
- Eight-week failure (post-20% gain, close below prior 10-day low within ~8 weeks).
- Profit take at +20% **unless** power-play hold window applies:
  - Power play is triggered if +20% is reached within 15 days.
  - Profit-taking is disabled for 40 days after entry if power play is active.

**Add-on (pyramiding) rules:**
- Only in market UPTREND.
- Max 2 add-ons per position.
- Requires price ≥ 2.5% above entry and, if already added, ≥ 2% above last add.
- Skips if price is extended: above 110% of the initial 5% pivot buy range.

**Order conflict resolution (runner):**
1. Stop-loss orders
2. Sell signals (SMA50, climax, eight-week, profit-take)
3. Add-on buys (only if no stop/sell for that symbol)
4. New entries (filtered to avoid symbols already being sold)

## 3) Frozen config defaults (v1.0)

These defaults are **frozen** for `v1.0-book-faithful`. Changing them requires bumping `CONFIG_VERSION` and updating this document.

| Field | Default |
| --- | --- |
| account_equity | 1000.0 |
| max_positions | 2 |
| min_price | 12.0 |
| min_history_days | 250 |
| min_avg_dollar_volume | 20000000.0 |
| max_pivot_buy_pct | 0.05 |
| stop_loss_pct | 0.08 |
| rs_min_percentile | 80.0 |
| distribution_warning | 3 |
| distribution_off | 5 |
| distribution_window | 25 |
| ftd_min_gain | 0.01 |
| ftd_min_day | 4 |
| ftd_max_day | 10 |

## 4) What we don’t do (explicit boundaries)

- No intraday indicators or minute-level data.
- No discretionary LLM overrides of risk rules (stops/sells).
- No earnings calendar optimization or forward guidance parsing.
- No social sentiment or news beyond FMP press releases.
- No pattern library beyond flat base (no cup-with-handle, etc.).
- No leverage, margin, options, or shorting.
- No live trading by default; paper guard blocks non-paper Alpaca without explicit acknowledgment.
- No performance claims; this is a rules engine requiring validation.

## 5) Safety + operational guardrails

- **Paper vs live:** Execute-paper enforces Alpaca paper endpoints and requires explicit `REQUIRE_PAPER_ACK`.
- **Environment variables:** See `docs/run.md` for required keys.
- **Validation:** Users must run walk-forward and paper trading before any live use.
