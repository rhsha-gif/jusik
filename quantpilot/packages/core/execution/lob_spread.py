from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from quantpilot.packages.core.schemas import utc_now


Side = Literal["buy", "sell"]


@dataclass(frozen=True)
class BookLevel:
    price: float
    quantity: float


@dataclass(frozen=True)
class L2OrderBook:
    symbol: str
    bids: list[BookLevel]
    asks: list[BookLevel]
    timestamp: datetime
    sequence_gap: bool = False


@dataclass(frozen=True)
class InstrumentMicrostructure:
    symbol: str
    asset_class: str
    tick_size: float
    lot_size: float = 1.0
    price_limit_up: float | None = None
    price_limit_down: float | None = None
    session_phase: str = "CONTINUOUS"
    max_staleness_seconds: int = 30
    max_spread_ticks_hard: int = 20
    maker_fee_bps: float = 0.0
    taxes_bps: float = 0.0
    rebate_bps: float = 0.0


@dataclass(frozen=True)
class LOBProfile:
    levels: int
    imbalance_decay: float
    microprice_weight: float
    vamp_weight: float
    ofi_tick_weight: float
    trade_sign_weight: float
    reversion_weight: float
    z_cap: float
    min_half_ticks: float
    normal_max_half_ticks: float
    stress_max_half_ticks: float
    gamma: float
    fill_k: float
    min_ev_ticks: float
    inventory_skew_per_order_unit_ticks: float
    adverse_alpha_weight: float = 0.0
    latency_ticks: float = 0.0
    regime_buffer_ticks: float = 0.0


@dataclass(frozen=True)
class LOBFeatures:
    mid_price: float
    spread: float
    spread_ticks: int
    top_bid_depth: float
    top_ask_depth: float
    imbalance: float
    microprice: float
    vamp: float
    weighted_depth_price: float


@dataclass(frozen=True)
class LimitPriceDecision:
    allowed: bool
    reason: str
    limit_price: float | None
    fair_price: float
    reservation_price: float
    half_spread_ticks: int
    expected_ev_ticks: float
    features: LOBFeatures


_EPS = 1e-9


_PROFILES: dict[str, LOBProfile] = {
    "KR_LARGECAP": LOBProfile(
        levels=10,
        imbalance_decay=0.30,
        microprice_weight=0.55,
        vamp_weight=0.20,
        ofi_tick_weight=0.10,
        trade_sign_weight=0.05,
        reversion_weight=0.05,
        z_cap=3.0,
        min_half_ticks=0.5,
        normal_max_half_ticks=1.0,
        stress_max_half_ticks=3.0,
        gamma=0.03,
        fill_k=5.0,
        min_ev_ticks=0.08,
        inventory_skew_per_order_unit_ticks=0.10,
    ),
    "KR_GENERAL": LOBProfile(
        levels=5,
        imbalance_decay=0.55,
        microprice_weight=0.30,
        vamp_weight=0.15,
        ofi_tick_weight=0.20,
        trade_sign_weight=0.15,
        reversion_weight=0.10,
        z_cap=2.0,
        min_half_ticks=1.0,
        normal_max_half_ticks=3.0,
        stress_max_half_ticks=6.0,
        gamma=0.05,
        fill_k=3.0,
        min_ev_ticks=0.15,
        inventory_skew_per_order_unit_ticks=0.20,
        regime_buffer_ticks=1.0,
    ),
    "KR_ETF": LOBProfile(
        levels=10,
        imbalance_decay=0.35,
        microprice_weight=0.25,
        vamp_weight=0.10,
        ofi_tick_weight=0.05,
        trade_sign_weight=0.05,
        reversion_weight=0.05,
        z_cap=2.0,
        min_half_ticks=1.0,
        normal_max_half_ticks=4.0,
        stress_max_half_ticks=8.0,
        gamma=0.04,
        fill_k=3.0,
        min_ev_ticks=0.10,
        inventory_skew_per_order_unit_ticks=0.15,
        regime_buffer_ticks=1.0,
    ),
    "US_EQUITY": LOBProfile(
        levels=10,
        imbalance_decay=0.35,
        microprice_weight=0.45,
        vamp_weight=0.20,
        ofi_tick_weight=0.15,
        trade_sign_weight=0.05,
        reversion_weight=0.05,
        z_cap=3.0,
        min_half_ticks=0.5,
        normal_max_half_ticks=3.0,
        stress_max_half_ticks=10.0,
        gamma=0.035,
        fill_k=4.0,
        min_ev_ticks=0.05,
        inventory_skew_per_order_unit_ticks=0.12,
    ),
}


def profile_for_asset_class(asset_class: str) -> LOBProfile:
    return _PROFILES.get(asset_class, _PROFILES["KR_GENERAL"])


def round_down_to_tick(price: float, tick_size: float) -> float:
    return round(math.floor((price + _EPS) / tick_size) * tick_size, 10)


def round_up_to_tick(price: float, tick_size: float) -> float:
    return round(math.ceil((price - _EPS) / tick_size) * tick_size, 10)


def _sorted_bids(book: L2OrderBook) -> list[BookLevel]:
    return sorted(book.bids, key=lambda level: level.price, reverse=True)


def _sorted_asks(book: L2OrderBook) -> list[BookLevel]:
    return sorted(book.asks, key=lambda level: level.price)


def data_quality_gate(
    book: L2OrderBook,
    instrument: InstrumentMicrostructure,
    *,
    now: datetime | None = None,
) -> tuple[bool, str]:
    current_time = now or utc_now()
    if book.sequence_gap:
        return False, "sequence_gap"
    if instrument.tick_size <= 0:
        return False, "invalid_tick_size"
    if not book.bids or not book.asks:
        return False, "empty_book"

    bids = _sorted_bids(book)
    asks = _sorted_asks(book)
    best_bid = bids[0]
    best_ask = asks[0]
    if best_bid.price <= 0 or best_ask.price <= 0 or best_bid.quantity <= 0 or best_ask.quantity <= 0:
        return False, "invalid_bbo"
    if best_bid.price >= best_ask.price:
        return False, "locked_or_crossed_book"

    age_seconds = (current_time - book.timestamp).total_seconds()
    if age_seconds < 0 or age_seconds > instrument.max_staleness_seconds:
        return False, "stale_book"

    spread_ticks = (best_ask.price - best_bid.price) / instrument.tick_size
    if spread_ticks > instrument.max_spread_ticks_hard:
        return False, "abnormal_spread"
    if instrument.session_phase in {"HALT", "VI", "LULD"}:
        return False, "halt_or_volatility_pause"
    allowed_phases = {"REGULAR", "EXTENDED"} if instrument.asset_class == "US_EQUITY" else {"CONTINUOUS"}
    if instrument.session_phase not in allowed_phases:
        return False, "not_continuous_session"
    return True, "ok"


def compute_lob_features(
    book: L2OrderBook,
    instrument: InstrumentMicrostructure,
    *,
    profile: LOBProfile | None = None,
) -> LOBFeatures:
    active_profile = profile or profile_for_asset_class(instrument.asset_class)
    levels = max(1, active_profile.levels)
    bids = _sorted_bids(book)[:levels]
    asks = _sorted_asks(book)[:levels]
    best_bid = bids[0]
    best_ask = asks[0]
    mid = (best_bid.price + best_ask.price) / 2
    spread = best_ask.price - best_bid.price
    spread_ticks = int(round(spread / instrument.tick_size))

    weighted_bid_qty = 0.0
    weighted_ask_qty = 0.0
    for index, level in enumerate(bids):
        weight = math.exp(-active_profile.imbalance_decay * index)
        weighted_bid_qty += weight * level.quantity
    for index, level in enumerate(asks):
        weight = math.exp(-active_profile.imbalance_decay * index)
        weighted_ask_qty += weight * level.quantity
    imbalance = (weighted_bid_qty - weighted_ask_qty) / max(weighted_bid_qty + weighted_ask_qty, _EPS)

    top_depth = best_bid.quantity + best_ask.quantity
    microprice = (best_ask.price * best_bid.quantity + best_bid.price * best_ask.quantity) / max(top_depth, _EPS)

    paired_count = min(len(bids), len(asks))
    cross_notional = 0.0
    own_side_notional = 0.0
    depth = 0.0
    for bid, ask in zip(bids[:paired_count], asks[:paired_count]):
        cross_notional += bid.price * ask.quantity + ask.price * bid.quantity
        own_side_notional += bid.price * bid.quantity + ask.price * ask.quantity
        depth += bid.quantity + ask.quantity
    vamp = cross_notional / max(depth, _EPS)
    weighted_depth_price = own_side_notional / max(depth, _EPS)

    return LOBFeatures(
        mid_price=mid,
        spread=spread,
        spread_ticks=spread_ticks,
        top_bid_depth=best_bid.quantity,
        top_ask_depth=best_ask.quantity,
        imbalance=round(imbalance, 10),
        microprice=microprice,
        vamp=vamp,
        weighted_depth_price=weighted_depth_price,
    )


def _clip(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def estimate_fair_price(
    features: LOBFeatures,
    instrument: InstrumentMicrostructure,
    *,
    profile: LOBProfile | None = None,
    z_ofi: float = 0.0,
    z_trade_sign: float = 0.0,
    short_term_reversion_ticks: float = 0.0,
) -> float:
    active_profile = profile or profile_for_asset_class(instrument.asset_class)
    z_cap = active_profile.z_cap
    return (
        features.mid_price
        + active_profile.microprice_weight * (features.microprice - features.mid_price)
        + active_profile.vamp_weight * (features.vamp - features.mid_price)
        + active_profile.ofi_tick_weight * instrument.tick_size * _clip(z_ofi, -z_cap, z_cap)
        + active_profile.trade_sign_weight * instrument.tick_size * _clip(z_trade_sign, -z_cap, z_cap)
        - active_profile.reversion_weight * short_term_reversion_ticks * instrument.tick_size
    )


def _avellaneda_half_spread_ticks(
    *,
    gamma: float,
    sigma_ticks: float,
    tau_seconds: float,
    fill_k: float,
) -> float:
    if gamma <= 0 or fill_k <= 0:
        raise ValueError("gamma and fill_k must be positive")
    tau = max(tau_seconds, _EPS)
    sigma = max(sigma_ticks, _EPS)
    return 0.5 * (gamma * sigma * sigma * tau + (2 / gamma) * math.log(1 + gamma / fill_k))


def _fee_ticks(price: float, instrument: InstrumentMicrostructure) -> float:
    bps = instrument.maker_fee_bps + instrument.taxes_bps - instrument.rebate_bps
    return max(0.0, price * bps / 10_000 / instrument.tick_size)


def _half_spread_ticks(
    *,
    fair_price: float,
    features: LOBFeatures,
    instrument: InstrumentMicrostructure,
    profile: LOBProfile,
    sigma_ticks: float,
    quote_horizon_seconds: float,
) -> int:
    h_as = _avellaneda_half_spread_ticks(
        gamma=profile.gamma,
        sigma_ticks=sigma_ticks,
        tau_seconds=quote_horizon_seconds,
        fill_k=profile.fill_k,
    )
    alpha_ticks = abs(fair_price - features.mid_price) / instrument.tick_size
    adverse_ticks = profile.adverse_alpha_weight * alpha_ticks
    raw_half_ticks = max(
        profile.min_half_ticks,
        h_as
        + _fee_ticks(features.mid_price, instrument)
        + adverse_ticks
        + profile.latency_ticks
        + profile.regime_buffer_ticks,
    )
    capped_half_ticks = min(raw_half_ticks, profile.normal_max_half_ticks)
    return max(1, int(math.ceil(capped_half_ticks)))


def _inventory_skew_ticks(
    *,
    position_qty: float,
    target_position_qty: float,
    order_unit_qty: float,
    profile: LOBProfile,
) -> float:
    q_norm = (position_qty - target_position_qty) / max(order_unit_qty, _EPS)
    return q_norm * profile.inventory_skew_per_order_unit_ticks


def limit_price_decision(
    *,
    side: Side,
    book: L2OrderBook,
    instrument: InstrumentMicrostructure,
    now: datetime | None = None,
    profile: LOBProfile | None = None,
    position_qty: float = 0.0,
    target_position_qty: float = 0.0,
    order_unit_qty: float = 1.0,
    sigma_ticks: float = 1.0,
    quote_horizon_seconds: float = 1.0,
) -> LimitPriceDecision:
    current_time = now or utc_now()
    ok, reason = data_quality_gate(book, instrument, now=current_time)
    active_profile = profile or profile_for_asset_class(instrument.asset_class)
    if not ok:
        empty_features = LOBFeatures(
            mid_price=0.0,
            spread=0.0,
            spread_ticks=0,
            top_bid_depth=0.0,
            top_ask_depth=0.0,
            imbalance=0.0,
            microprice=0.0,
            vamp=0.0,
            weighted_depth_price=0.0,
        )
        return LimitPriceDecision(
            allowed=False,
            reason=reason,
            limit_price=None,
            fair_price=0.0,
            reservation_price=0.0,
            half_spread_ticks=0,
            expected_ev_ticks=0.0,
            features=empty_features,
        )

    features = compute_lob_features(book, instrument, profile=active_profile)
    fair = estimate_fair_price(features, instrument, profile=active_profile)
    skew_ticks = _inventory_skew_ticks(
        position_qty=position_qty,
        target_position_qty=target_position_qty,
        order_unit_qty=order_unit_qty,
        profile=active_profile,
    )
    reservation = fair - skew_ticks * instrument.tick_size
    half_ticks = _half_spread_ticks(
        fair_price=fair,
        features=features,
        instrument=instrument,
        profile=active_profile,
        sigma_ticks=sigma_ticks,
        quote_horizon_seconds=quote_horizon_seconds,
    )

    best_bid = _sorted_bids(book)[0].price
    best_ask = _sorted_asks(book)[0].price
    raw_bid = reservation - half_ticks * instrument.tick_size
    raw_ask = reservation + half_ticks * instrument.tick_size
    bid_price = round_down_to_tick(raw_bid, instrument.tick_size)
    ask_price = round_up_to_tick(raw_ask, instrument.tick_size)

    if bid_price >= best_ask:
        bid_price = best_bid
    if ask_price <= best_bid:
        ask_price = best_ask

    if side == "buy":
        passive_cap = round_down_to_tick(best_ask - instrument.tick_size, instrument.tick_size)
        candidate = min(max(bid_price, best_bid), passive_cap)
        edge_ticks = (fair - candidate) / instrument.tick_size - _fee_ticks(candidate, instrument)
    else:
        passive_floor = round_up_to_tick(best_bid + instrument.tick_size, instrument.tick_size)
        candidate = max(min(ask_price, best_ask), passive_floor)
        edge_ticks = (candidate - fair) / instrument.tick_size - _fee_ticks(candidate, instrument)

    if instrument.price_limit_down is not None and candidate < instrument.price_limit_down:
        return LimitPriceDecision(
            allowed=False,
            reason="price_below_limit",
            limit_price=None,
            fair_price=fair,
            reservation_price=reservation,
            half_spread_ticks=half_ticks,
            expected_ev_ticks=edge_ticks,
            features=features,
        )
    if instrument.price_limit_up is not None and candidate > instrument.price_limit_up:
        return LimitPriceDecision(
            allowed=False,
            reason="price_above_limit",
            limit_price=None,
            fair_price=fair,
            reservation_price=reservation,
            half_spread_ticks=half_ticks,
            expected_ev_ticks=edge_ticks,
            features=features,
        )
    if edge_ticks <= active_profile.min_ev_ticks:
        return LimitPriceDecision(
            allowed=False,
            reason="expected_value_too_low",
            limit_price=None,
            fair_price=fair,
            reservation_price=reservation,
            half_spread_ticks=half_ticks,
            expected_ev_ticks=edge_ticks,
            features=features,
        )

    return LimitPriceDecision(
        allowed=True,
        reason="ok",
        limit_price=round(candidate, 10),
        fair_price=fair,
        reservation_price=reservation,
        half_spread_ticks=half_ticks,
        expected_ev_ticks=edge_ticks,
        features=features,
    )
