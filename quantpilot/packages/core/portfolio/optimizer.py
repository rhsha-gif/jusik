from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from quantpilot.packages.core.portfolio.optimizer_types import (
    ExpectedReturnRiskProxy,
    OptimizationInput,
    OptimizationResult,
    TargetWeight,
)
from quantpilot.packages.core.schemas import PortfolioSnapshot, Signal, SignalAction


_EPSILON = 1e-9


@dataclass
class _Candidate:
    symbol: str
    sector: str
    current_weight: float
    target_weight: float
    expected_return: float
    volatility: float
    score: float
    reason_codes: list[str] = field(default_factory=list)
    constrained_by: list[str] = field(default_factory=list)


def _symbol(value: str) -> str:
    return value.strip().upper()


def _weight(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 6)


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _current_weights(snapshot: PortfolioSnapshot) -> dict[str, float]:
    weights: dict[str, float] = {}
    for position in snapshot.positions:
        symbol = _symbol(position.symbol)
        weights[symbol] = weights.get(symbol, 0.0) + position.market_value / snapshot.equity
    return weights


def _position_sectors(snapshot: PortfolioSnapshot) -> dict[str, str]:
    sectors: dict[str, str] = {}
    for position in snapshot.positions:
        sectors.setdefault(_symbol(position.symbol), position.sector.strip().lower() or "unknown")
    return sectors


def _candidate_symbols(signals: list[Signal]) -> list[str]:
    return _unique(_symbol(signal.symbol) for signal in signals)


def _other_position_weights(
    *,
    snapshot: PortfolioSnapshot,
    candidate_symbols: set[str],
    sector_metadata: dict[str, str],
) -> dict[str, float]:
    weights: dict[str, float] = {}
    for position in snapshot.positions:
        symbol = _symbol(position.symbol)
        if symbol in candidate_symbols:
            continue
        sector = sector_metadata.get(symbol, position.sector.strip().lower() or "unknown")
        weights[sector] = weights.get(sector, 0.0) + position.market_value / snapshot.equity
    return weights


def _target_from_signal(
    *,
    signal: Signal,
    proxy: ExpectedReturnRiskProxy,
    current_weight: float,
    max_position_weight: float,
) -> tuple[float, float, list[str]]:
    action = signal.action
    strength = max(0.0, min(1.0, signal.strength))
    expected_return = max(0.0, proxy.expected_return)
    score = expected_return * max(strength, 0.01) / (1.0 + proxy.volatility)
    reason_codes = [f"signal_action_{action.value}", *signal.reason_codes]
    if not proxy.calibrated:
        reason_codes.append("uncalibrated_expected_return_risk_proxy")

    if action in {SignalAction.blocked, SignalAction.exit, SignalAction.buy_wait}:
        return 0.0, score, reason_codes
    if action == SignalAction.trim:
        return min(max_position_weight, max(0.0, current_weight * 0.5)), score, reason_codes
    if action == SignalAction.hold:
        return min(max_position_weight, current_weight), score, reason_codes
    if action == SignalAction.buy_ready:
        if score <= 0:
            return 0.0, score, reason_codes
        return min(max_position_weight, max(0.01, score * max_position_weight)), score, reason_codes
    return min(max_position_weight, current_weight), score, reason_codes


class DeterministicPortfolioOptimizer:
    """Fixture-first constrained optimizer for target weights.

    The optimizer is deterministic and long-only. It never submits orders and
    returns fail-closed/no-trade results instead of raising runtime planning
    errors to downstream order code.
    """

    def optimize(self, request: OptimizationInput) -> OptimizationResult:
        try:
            return self._optimize(request)
        except Exception as exc:
            return self._fail_closed(request, ["optimizer_exception", type(exc).__name__])

    def _optimize(self, request: OptimizationInput) -> OptimizationResult:
        if not request.signals:
            return OptimizationResult(
                status="no_trade",
                cash_target_weight=_weight(request.snapshot.cash_weight),
                turnover_weight=0.0,
                reason_codes=["no_signals"],
                constraints_applied=[],
                proxy_metadata=request.proxy_metadata,
            )

        missing = [symbol for symbol in _candidate_symbols(request.signals) if symbol not in request.proxies]
        if missing:
            return self._fail_closed(
                request,
                ["missing_expected_return_risk_proxy", *[f"missing_proxy_{symbol}" for symbol in missing]],
            )

        candidates = self._build_candidates(request)
        if not candidates:
            return OptimizationResult(
                status="no_trade",
                cash_target_weight=_weight(request.snapshot.cash_weight),
                turnover_weight=0.0,
                reason_codes=["no_optimizer_candidates"],
                constraints_applied=[],
                proxy_metadata=request.proxy_metadata,
            )

        self._apply_max_order_weight(request, candidates)
        self._apply_sector_caps(request, candidates)
        self._apply_cash_buffer(request, candidates)
        self._apply_turnover_cap(request, candidates)
        self._apply_rebalance_band(request, candidates)

        violations = self._constraint_violations(request, candidates)
        if violations:
            return self._fail_closed(request, ["constraints_infeasible", *violations])

        turnover = self._turnover(candidates)
        status = "optimized" if turnover > _EPSILON else "no_trade"
        reason_codes = ["constraints_satisfied"] if status == "optimized" else ["no_target_weight_changes"]
        return self._result(request, candidates, status=status, reason_codes=reason_codes)

    def _build_candidates(self, request: OptimizationInput) -> list[_Candidate]:
        current = _current_weights(request.snapshot)
        position_sectors = _position_sectors(request.snapshot)
        candidates: list[_Candidate] = []
        seen: set[str] = set()
        for signal in request.signals:
            symbol = _symbol(signal.symbol)
            if symbol in seen:
                continue
            seen.add(symbol)
            proxy = request.proxies[symbol]
            current_weight = current.get(symbol, 0.0)
            sector = request.sector_metadata.get(symbol, position_sectors.get(symbol, "unknown"))
            target, score, reason_codes = _target_from_signal(
                signal=signal,
                proxy=proxy,
                current_weight=current_weight,
                max_position_weight=request.constraints.max_position_weight,
            )
            constrained_by: list[str] = []
            if target >= request.constraints.max_position_weight - _EPSILON:
                constrained_by.append("max_position_weight")
            candidates.append(
                _Candidate(
                    symbol=symbol,
                    sector=sector,
                    current_weight=current_weight,
                    target_weight=max(0.0, target),
                    expected_return=proxy.expected_return,
                    volatility=proxy.volatility,
                    score=score,
                    reason_codes=_unique(reason_codes),
                    constrained_by=constrained_by,
                )
            )
        return candidates

    def _other_sector_weights(self, request: OptimizationInput, candidates: list[_Candidate]) -> dict[str, float]:
        return _other_position_weights(
            snapshot=request.snapshot,
            candidate_symbols={candidate.symbol for candidate in candidates},
            sector_metadata=request.sector_metadata,
        )

    def _invested_weight(self, request: OptimizationInput, candidates: list[_Candidate]) -> float:
        other = sum(self._other_sector_weights(request, candidates).values())
        return other + sum(candidate.target_weight for candidate in candidates)

    def _sector_totals(self, request: OptimizationInput, candidates: list[_Candidate]) -> dict[str, float]:
        totals = self._other_sector_weights(request, candidates)
        for candidate in candidates:
            totals[candidate.sector] = totals.get(candidate.sector, 0.0) + candidate.target_weight
        return totals

    def _reduce_candidates(self, candidates: list[_Candidate], amount: float, reason: str) -> float:
        remaining = amount
        reducible = sorted(
            (candidate for candidate in candidates if candidate.target_weight > _EPSILON),
            key=lambda candidate: (candidate.score, candidate.symbol),
        )
        for candidate in reducible:
            if remaining <= _EPSILON:
                break
            reduction = min(candidate.target_weight, remaining)
            candidate.target_weight -= reduction
            remaining -= reduction
            candidate.constrained_by.append(reason)
        return remaining

    def _apply_max_order_weight(self, request: OptimizationInput, candidates: list[_Candidate]) -> None:
        max_order_weight = request.constraints.max_order_weight
        if max_order_weight is None:
            return
        for candidate in candidates:
            delta = candidate.target_weight - candidate.current_weight
            if abs(delta) <= max_order_weight + _EPSILON:
                continue
            if delta > 0:
                candidate.target_weight = candidate.current_weight + max_order_weight
            else:
                candidate.target_weight = max(0.0, candidate.current_weight - max_order_weight)
            candidate.constrained_by.append("max_order_weight")

    def _apply_sector_caps(self, request: OptimizationInput, candidates: list[_Candidate]) -> None:
        cap = request.constraints.max_sector_weight
        for sector in sorted(self._sector_totals(request, candidates)):
            total = self._sector_totals(request, candidates).get(sector, 0.0)
            if total <= cap + _EPSILON:
                continue
            sector_candidates = [candidate for candidate in candidates if candidate.sector == sector]
            self._reduce_candidates(sector_candidates, total - cap, "max_sector_weight")

    def _apply_cash_buffer(self, request: OptimizationInput, candidates: list[_Candidate]) -> None:
        max_invested = 1.0 - request.constraints.min_cash_weight
        invested = self._invested_weight(request, candidates)
        if invested <= max_invested + _EPSILON:
            return
        self._reduce_candidates(candidates, invested - max_invested, "min_cash_weight")

    def _apply_turnover_cap(self, request: OptimizationInput, candidates: list[_Candidate]) -> None:
        turnover = self._turnover(candidates)
        cap = request.constraints.max_turnover_weight
        if turnover <= cap + _EPSILON:
            return
        if cap <= _EPSILON:
            for candidate in candidates:
                if abs(candidate.target_weight - candidate.current_weight) > _EPSILON:
                    candidate.target_weight = candidate.current_weight
                    candidate.constrained_by.append("max_turnover_weight")
            return
        scale = cap / turnover
        for candidate in candidates:
            delta = candidate.target_weight - candidate.current_weight
            if abs(delta) <= _EPSILON:
                continue
            candidate.target_weight = candidate.current_weight + delta * scale
            candidate.constrained_by.append("max_turnover_weight")

    def _apply_rebalance_band(self, request: OptimizationInput, candidates: list[_Candidate]) -> None:
        band = request.constraints.rebalance_band
        if band <= _EPSILON:
            return
        for candidate in candidates:
            if abs(candidate.target_weight - candidate.current_weight) < band:
                candidate.target_weight = candidate.current_weight
                candidate.constrained_by.append("rebalance_band")

    def _turnover(self, candidates: list[_Candidate]) -> float:
        return sum(abs(candidate.target_weight - candidate.current_weight) for candidate in candidates)

    def _constraint_violations(self, request: OptimizationInput, candidates: list[_Candidate]) -> list[str]:
        constraints = request.constraints
        violations: list[str] = []
        if any(candidate.target_weight > constraints.max_position_weight + 1e-6 for candidate in candidates):
            violations.append("max_position_weight")
        if self._invested_weight(request, candidates) > 1.0 - constraints.min_cash_weight + 1e-6:
            violations.append("min_cash_weight")
        for sector, total in self._sector_totals(request, candidates).items():
            if total > constraints.max_sector_weight + 1e-6:
                violations.append(f"max_sector_weight_{sector}")
        if self._turnover(candidates) > constraints.max_turnover_weight + 1e-6:
            violations.append("max_turnover_weight")
        return _unique(violations)

    def _result(
        self,
        request: OptimizationInput,
        candidates: list[_Candidate],
        *,
        status: str,
        reason_codes: list[str],
    ) -> OptimizationResult:
        invested = self._invested_weight(request, candidates)
        target_weights = [
            TargetWeight(
                symbol=candidate.symbol,
                sector=candidate.sector,
                current_weight=_weight(candidate.current_weight),
                target_weight=_weight(candidate.target_weight),
                expected_return=candidate.expected_return,
                volatility=candidate.volatility,
                score=round(candidate.score, 6),
                reason_codes=candidate.reason_codes,
                constrained_by=_unique(candidate.constrained_by),
            )
            for candidate in sorted(candidates, key=lambda item: item.symbol)
        ]
        constraints_applied = _unique(
            constraint for candidate in candidates for constraint in candidate.constrained_by
        )
        return OptimizationResult(
            status=status,  # type: ignore[arg-type]
            target_weights=target_weights,
            cash_target_weight=_weight(1.0 - invested),
            turnover_weight=round(self._turnover(candidates), 6),
            reason_codes=_unique(reason_codes),
            constraints_applied=constraints_applied,
            proxy_metadata=request.proxy_metadata,
        )

    def _fail_closed(self, request: OptimizationInput, reason_codes: list[str]) -> OptimizationResult:
        current = _current_weights(request.snapshot)
        position_sectors = _position_sectors(request.snapshot)
        target_weights: list[TargetWeight] = []
        for signal in request.signals:
            symbol = _symbol(signal.symbol)
            proxy = request.proxies.get(symbol)
            sector = request.sector_metadata.get(symbol, position_sectors.get(symbol, "unknown"))
            target_weights.append(
                TargetWeight(
                    symbol=symbol,
                    sector=sector,
                    current_weight=_weight(current.get(symbol, 0.0)),
                    target_weight=_weight(current.get(symbol, 0.0)),
                    expected_return=proxy.expected_return if proxy else 0.0,
                    volatility=proxy.volatility if proxy else 0.0,
                    score=0.0,
                    reason_codes=["fail_closed_no_trade"],
                    constrained_by=["fail_closed_no_trade"],
                )
            )
        return OptimizationResult(
            status="fail_closed",
            target_weights=target_weights,
            cash_target_weight=_weight(request.snapshot.cash_weight),
            turnover_weight=0.0,
            reason_codes=_unique(reason_codes),
            constraints_applied=["fail_closed_no_trade"],
            proxy_metadata=request.proxy_metadata,
        )
