from bot.walk_forward import _with_cost


def test_cost_slips_against_us_both_ways():
    assert _with_cost(100.0, "buy", 25.0) == 100.25
    assert _with_cost(100.0, "sell", 25.0) == 99.75


def test_zero_cost_is_a_noop():
    assert _with_cost(100.0, "buy", 0.0) == 100.0


def test_round_trip_at_flat_price_loses_money():
    """A buy and sell at the same quote must lose the round-trip cost."""
    buy = _with_cost(100.0, "buy", 25.0)
    sell = _with_cost(100.0, "sell", 25.0)
    assert sell < buy
    assert round((buy - sell) / 100.0 * 10_000) == 50  # 50bps round trip
