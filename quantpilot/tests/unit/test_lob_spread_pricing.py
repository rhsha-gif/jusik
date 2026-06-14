from __future__ import annotations

from datetime import timedelta

from quantpilot.packages.core.execution.lob_spread import (
    BookLevel,
    InstrumentMicrostructure,
    L2OrderBook,
    data_quality_gate,
    limit_price_decision,
)
from quantpilot.packages.core.policy.parser import parse_policy_text
from quantpilot.packages.core.portfolio.planner import build_portfolio_plan, fixture_portfolio_snapshot
from quantpilot.packages.core.schemas import Signal, SignalAction, utc_now


def _buy_signal(symbol: str = "AAA") -> Signal:
    return Signal(
        strategy_id="test_lob_spread",
        recipe_version="1",
        symbol=symbol,
        action=SignalAction.buy_ready,
        strength=0.8,
        reason="test buy-ready signal",
    )


def _book(
    *,
    bid: float = 99.0,
    ask: float = 101.0,
    bid_qty: float = 10_000,
    ask_qty: float = 1_000,
) -> L2OrderBook:
    return L2OrderBook(
        symbol="AAA",
        bids=[BookLevel(price=bid, quantity=bid_qty), BookLevel(price=bid - 1, quantity=7_500)],
        asks=[BookLevel(price=ask, quantity=ask_qty), BookLevel(price=ask + 1, quantity=2_000)],
        timestamp=utc_now(),
    )


def test_lob_price_decision_uses_passive_buy_price_on_valid_tick() -> None:
    instrument = InstrumentMicrostructure(symbol="AAA", asset_class="KR_LARGECAP", tick_size=1.0)

    decision = limit_price_decision(
        side="buy",
        book=_book(),
        instrument=instrument,
        now=utc_now(),
    )

    assert decision.allowed
    assert decision.limit_price == 99.0
    assert decision.limit_price < 101.0
    assert decision.fair_price > decision.features.mid_price
    assert decision.expected_ev_ticks > 0


def test_lob_quality_gate_blocks_locked_book() -> None:
    instrument = InstrumentMicrostructure(symbol="AAA", asset_class="KR_LARGECAP", tick_size=1.0)
    locked = L2OrderBook(
        symbol="AAA",
        bids=[BookLevel(price=100, quantity=1_000)],
        asks=[BookLevel(price=100, quantity=1_000)],
        timestamp=utc_now(),
    )

    ok, reason = data_quality_gate(locked, instrument, now=utc_now())

    assert not ok
    assert reason == "locked_or_crossed_book"


def test_lob_quality_gate_blocks_stale_book() -> None:
    instrument = InstrumentMicrostructure(
        symbol="AAA",
        asset_class="KR_LARGECAP",
        tick_size=1.0,
        max_staleness_seconds=30,
    )
    stale = _book()
    stale = L2OrderBook(
        symbol=stale.symbol,
        bids=stale.bids,
        asks=stale.asks,
        timestamp=utc_now() - timedelta(seconds=31),
    )

    ok, reason = data_quality_gate(stale, instrument, now=utc_now())

    assert not ok
    assert reason == "stale_book"


def test_lob_quality_gate_allows_us_regular_session() -> None:
    instrument = InstrumentMicrostructure(
        symbol="MSFT",
        asset_class="US_EQUITY",
        tick_size=0.01,
        session_phase="REGULAR",
    )
    book = L2OrderBook(
        symbol="MSFT",
        bids=[BookLevel(price=100.00, quantity=1_000)],
        asks=[BookLevel(price=100.01, quantity=1_000)],
        timestamp=utc_now(),
    )

    ok, reason = data_quality_gate(book, instrument, now=utc_now())

    assert ok
    assert reason == "ok"


def test_portfolio_planner_uses_lob_limit_price_when_book_is_available() -> None:
    policy = parse_policy_text("fixture")
    snapshot = fixture_portfolio_snapshot()
    instrument = InstrumentMicrostructure(symbol="AAA", asset_class="KR_LARGECAP", tick_size=1.0)

    plan = build_portfolio_plan(
        policy=policy,
        signals=[_buy_signal()],
        snapshot=snapshot,
        quotes={"AAA": 105.0},
        order_books={"AAA": _book()},
        instruments={"AAA": instrument},
    )

    assert len(plan.order_intents) == 1
    assert plan.order_intents[0].limit_price == 99.0
    assert plan.order_intents[0].quantity == round(plan.order_intents[0].notional / 99.0, 6)


def test_portfolio_planner_blocks_new_order_when_lob_book_is_unsafe() -> None:
    policy = parse_policy_text("fixture")
    instrument = InstrumentMicrostructure(symbol="AAA", asset_class="KR_LARGECAP", tick_size=1.0)
    unsafe_book = L2OrderBook(
        symbol="AAA",
        bids=[BookLevel(price=100, quantity=1_000)],
        asks=[BookLevel(price=100, quantity=1_000)],
        timestamp=utc_now(),
    )

    plan = build_portfolio_plan(
        policy=policy,
        signals=[_buy_signal()],
        snapshot=fixture_portfolio_snapshot(),
        quotes={"AAA": 105.0},
        order_books={"AAA": unsafe_book},
        instruments={"AAA": instrument},
    )

    assert plan.order_intents == []
    assert plan.target_weights["AAA"] == 0.0


def test_portfolio_planner_requires_instrument_when_lob_book_is_available() -> None:
    policy = parse_policy_text("fixture")

    plan = build_portfolio_plan(
        policy=policy,
        signals=[_buy_signal()],
        snapshot=fixture_portfolio_snapshot(),
        quotes={"AAA": 105.0},
        order_books={"AAA": _book()},
        instruments={},
    )

    assert plan.order_intents == []
    assert plan.target_weights["AAA"] == 0.0
