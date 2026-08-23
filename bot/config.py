from dataclasses import dataclass


CONFIG_VERSION = "v1.0-book-faithful"


# These defaults are frozen for v1.0-book-faithful. Do not change without bumping
# CONFIG_VERSION and updating docs/canon.md.
@dataclass(frozen=True)
class BotConfig:
    account_equity: float = 1000.0
    max_positions: int = 2
    min_price: float = 12.0
    min_history_days: int = 250
    min_avg_dollar_volume: float = 20_000_000.0
    # One-way transaction cost in bps, applied to every fill in the walk-forward
    # sim. 25bps/side = 50bps round trip, the value-weighted anomaly average in
    # NBER w20721. Zero here is how a backtest lies about a high-turnover screen.
    cost_bps: float = 25.0
    max_pivot_buy_pct: float = 0.05
    stop_loss_pct: float = 0.08
    rs_min_percentile: float = 80.0
    distribution_warning: int = 3
    distribution_off: int = 5
    distribution_window: int = 25
    ftd_min_gain: float = 0.01
    ftd_min_day: int = 4
    ftd_max_day: int = 10
