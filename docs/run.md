# Running the CAN SLIM bot

## v1.0 canon

The strategy rules and frozen defaults are defined in [`docs/canon.md`](canon.md). Do not change strategy logic after v1.0 without updating the canon document and bumping `CONFIG_VERSION`.

## Environment variables

Set these before running the CLI:

- `ALPACA_KEY_ID`
- `ALPACA_SECRET_KEY`
- `ALPACA_BASE_URL`
- `FMP_API_KEY`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `ALPACA_PAPER` (set to `1` for paper guard)
- `REQUIRE_PAPER_ACK` (set to `I_UNDERSTAND_THIS_IS_PAPER` for paper guard)

## Usage

```
python -m bot.cli --symbols symbols.txt --end-date 2025-01-02 --dry-run
python -m bot.cli --symbols symbols.txt --end-date 2025-01-02 --execute
python -m bot.cli --symbols symbols.txt --walk-forward --start 2024-01-01 --end 2024-06-30
python -m bot.cli --symbols symbols.txt --execute-paper --confirm
```

The `--execute` flag submits market orders to Alpaca; without it, the CLI runs in dry-run mode and prints the proposed orders and log entries.

## Artifacts

Execute-paper runs create an audit bundle in `runs/YYYY-MM-DD/run_<timestamp>/`:

- `summary.json` (includes counts by reason)
- `market.json`
- `orders.csv`
- `candidates.csv`

Walk-forward outputs CSV artifacts to `runs/walk_forward_<YYYY-MM-DD>/`:

- `equity_curve.csv`
- `trades.csv`

Example: check CSV headers.

```
head -n 1 runs/2024-06-01/run_20240601_150000/orders.csv
```
