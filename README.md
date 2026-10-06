# CAN SLIM Bot (v1.0-book-faithful)

A daily CAN SLIM rules engine based on *How to Make Money in Stocks*. It scans after the close and places orders for the next open (paper or live, per configuration).

> **Warning:** This is not financial advice. The system is paper-first and requires validation before any live use.

## Canon

The authoritative strategy definition lives in [`docs/canon.md`](docs/canon.md).

## Quick start

### Dry run

```
python -m bot.cli --symbols symbols.txt --end-date 2025-01-02 --dry-run
```

### Execute paper scan

```
python -m bot.cli --symbols symbols.txt --execute-paper --confirm
```

### Walk-forward simulation

```
python -m bot.cli --symbols symbols.txt --walk-forward --start 2024-01-01 --end 2024-06-30
```

See [`docs/run.md`](docs/run.md) for full run and artifact details.

## Required environment variables

Core credentials:
- `ALPACA_KEY_ID`
- `ALPACA_SECRET_KEY`
- `ALPACA_BASE_URL`
- `FMP_API_KEY`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

Paper guard (execute-paper):
- `ALPACA_PAPER=1` **or** an Alpaca paper URL in `ALPACA_BASE_URL`
- `REQUIRE_PAPER_ACK=I_UNDERSTAND_THIS_IS_PAPER`

## Strategy version

- Strategy canon: `v1.0-book-faithful`
- Package version: `1.0.0`
