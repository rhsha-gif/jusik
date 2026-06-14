from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from quantpilot.packages.core.normalization import parse_float, symbol_key
from quantpilot.packages.core.schemas import CandidateUniverseItem, PortfolioSnapshot, UserPolicy
from quantpilot.packages.core.universe.ranking_types import CandidateScore, RankedCandidate, RankingComponentName, RankingExplanation


NEUTRAL_SCORE = 50.0
MAX_CANDIDATES_DEFAULT = 20

SCORE_WEIGHTS: dict[RankingComponentName, float] = {
    "theme": 0.16,
    "sector": 0.12,
    "liquidity": 0.18,
    "data_quality": 0.16,
    "volatility": 0.12,
    "correlation": 0.10,
    "existing_exposure": 0.10,
    "fundamental_availability": 0.06,
}


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 6)


def _symbol(value: str) -> str:
    return symbol_key(value)


def _as_float(value: Any) -> float | None:
    return parse_float(value)


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "y"}:
        return True
    if raw in {"0", "false", "no", "n"}:
        return False
    return None


def _normalized_strings(values: Sequence[Any] | None) -> set[str]:
    if isinstance(values, str):
        values = values.replace("|", ",").split(",")
    return {str(value).strip().lower() for value in values or [] if str(value).strip()}


def _ticker_metadata(
    ticker: str,
    provider_metadata: Mapping[str, Mapping[str, Any]] | None,
) -> Mapping[str, Any]:
    if provider_metadata is None:
        return {}
    ticker_key = _symbol(ticker)
    direct = provider_metadata.get(ticker_key)
    if direct is not None:
        return direct
    lower = provider_metadata.get(ticker_key.lower())
    if lower is not None:
        return lower
    securities = provider_metadata.get("securities")
    if isinstance(securities, Mapping):
        nested = securities.get(ticker_key) or securities.get(ticker_key.lower())
        if isinstance(nested, Mapping):
            return nested
    symbols = provider_metadata.get("symbols")
    if isinstance(symbols, Mapping):
        nested = symbols.get(ticker_key) or symbols.get(ticker_key.lower())
        if isinstance(nested, Mapping):
            return nested
    return {}


def _merged_metadata(security: Mapping[str, Any], provider: Mapping[str, Any]) -> dict[str, Any]:
    return {**security, **provider}


def _current_weight(snapshot: PortfolioSnapshot | None, ticker: str) -> float | None:
    if snapshot is None:
        return None
    symbol = _symbol(ticker)
    position_value = sum(position.market_value for position in snapshot.positions if _symbol(position.symbol) == symbol)
    return position_value / snapshot.equity


def _theme_score(policy: UserPolicy, metadata: Mapping[str, Any]) -> tuple[float, str, bool]:
    preferred = set(policy.preferred_themes)
    themes = _normalized_strings(metadata.get("themes"))
    if not preferred:
        return NEUTRAL_SCORE, "neutral: policy has no preferred themes", False
    if not themes:
        return NEUTRAL_SCORE, "neutral: candidate theme metadata unavailable", True
    matched = sorted(preferred.intersection(themes))
    if not matched:
        return 0.0, "candidate does not match preferred themes", False
    score = _clamp_score(100.0 * len(matched) / len(preferred))
    return score, f"matched preferred themes: {', '.join(matched)}", False


def _sector_score(policy: UserPolicy, candidate: CandidateUniverseItem) -> tuple[float, str, bool]:
    preferred = set(policy.preferred_sectors)
    if not preferred:
        return NEUTRAL_SCORE, "neutral: policy has no preferred sectors", False
    sector = candidate.sector.strip().lower()
    if not sector:
        return NEUTRAL_SCORE, "neutral: candidate sector metadata unavailable", True
    if sector in preferred:
        return 100.0, f"matched preferred sector: {sector}", False
    return 0.0, f"sector {sector} is outside preferred sectors", False


def _liquidity_score(policy: UserPolicy, metadata: Mapping[str, Any]) -> tuple[float, str, bool]:
    avg_daily_value = _as_float(metadata.get("avg_daily_value", metadata.get("average_daily_value")))
    if avg_daily_value is None:
        return NEUTRAL_SCORE, "neutral: average daily value unavailable", True
    if policy.min_avg_daily_value <= 0:
        return 100.0, "policy has no minimum average daily value", False
    ratio = avg_daily_value / policy.min_avg_daily_value
    score = _clamp_score(ratio * 100.0)
    return score, f"average daily value is {ratio:.2f}x policy minimum", False


def _data_quality_score(metadata: Mapping[str, Any], candidate: CandidateUniverseItem) -> tuple[float, str, bool]:
    explicit_score = _as_float(metadata.get("data_quality_score"))
    if explicit_score is not None:
        return _clamp_score(explicit_score), "provider data quality score supplied", False
    explicit_state = metadata.get("data_quality")
    if explicit_state is not None:
        state = str(explicit_state).strip().lower()
        if state in {"passed", "available", "clean", "usable"}:
            return 100.0, f"provider data quality is {state}", False
        if state in {"degraded", "stale"}:
            return 60.0, f"provider data quality is {state}", False
        if state in {"failed", "unavailable"}:
            return 0.0, f"provider data quality is {state}", False
    if candidate.data_ready:
        return 100.0, "candidate data_ready flag is true", False
    return 0.0, "candidate data_ready flag is false", False


def _volatility_score(metadata: Mapping[str, Any]) -> tuple[float, str, bool]:
    volatility = _as_float(metadata.get("volatility", metadata.get("annualized_volatility")))
    if volatility is None:
        return NEUTRAL_SCORE, "neutral: volatility metadata unavailable", True
    score = _clamp_score(100.0 - max(volatility, 0.0) * 100.0)
    return score, f"volatility penalty from annualized volatility {volatility:.4f}", False


def _correlation_score(metadata: Mapping[str, Any]) -> tuple[float, str, bool]:
    correlation = _as_float(metadata.get("correlation_to_portfolio", metadata.get("portfolio_correlation")))
    if correlation is None:
        return NEUTRAL_SCORE, "neutral: portfolio correlation metadata unavailable", True
    score = _clamp_score(100.0 - abs(correlation) * 100.0)
    return score, f"portfolio correlation is {correlation:.4f}", False


def _existing_exposure_score(
    policy: UserPolicy,
    snapshot: PortfolioSnapshot | None,
    candidate: CandidateUniverseItem,
) -> tuple[float, str, bool]:
    weight = _current_weight(snapshot, candidate.ticker)
    if weight is None:
        return NEUTRAL_SCORE, "neutral: portfolio snapshot unavailable", True
    if policy.max_position_weight <= 0:
        return NEUTRAL_SCORE, "neutral: max position weight unavailable", True
    score = _clamp_score(100.0 * (1.0 - min(weight / policy.max_position_weight, 1.0)))
    return score, f"current position weight is {weight:.4f}", False


def _fundamental_availability_score(metadata: Mapping[str, Any]) -> tuple[float, str, bool]:
    explicit_score = _as_float(metadata.get("fundamental_score", metadata.get("valuation_score")))
    if explicit_score is not None:
        return _clamp_score(explicit_score), "provider fundamental or valuation score supplied", False
    available = _as_bool(metadata.get("fundamental_available", metadata.get("valuation_available")))
    if available is True:
        return 100.0, "fundamental or valuation data is available", False
    if available is False:
        return 25.0, "fundamental or valuation data is explicitly unavailable", False
    return NEUTRAL_SCORE, "neutral: fundamental and valuation metadata unavailable", True


def _final_score(component_scores: dict[RankingComponentName, float]) -> float:
    return _clamp_score(sum(component_scores[name] * weight for name, weight in SCORE_WEIGHTS.items()))


def _candidate_score(
    *,
    policy: UserPolicy,
    candidate: CandidateUniverseItem,
    metadata: Mapping[str, Any],
    snapshot: PortfolioSnapshot | None,
) -> tuple[CandidateScore, RankingExplanation]:
    component_scores: dict[RankingComponentName, float] = {}
    explanations: dict[RankingComponentName, str] = {}
    unavailable: list[RankingComponentName] = []

    scorers: list[tuple[RankingComponentName, tuple[float, str, bool]]] = [
        ("theme", _theme_score(policy, metadata)),
        ("sector", _sector_score(policy, candidate)),
        ("liquidity", _liquidity_score(policy, metadata)),
        ("data_quality", _data_quality_score(metadata, candidate)),
        ("volatility", _volatility_score(metadata)),
        ("correlation", _correlation_score(metadata)),
        ("existing_exposure", _existing_exposure_score(policy, snapshot, candidate)),
        ("fundamental_availability", _fundamental_availability_score(metadata)),
    ]
    for name, (score, explanation, is_unavailable) in scorers:
        component_scores[name] = _clamp_score(score)
        explanations[name] = explanation
        if is_unavailable:
            unavailable.append(name)

    score = CandidateScore(
        **component_scores,
        final_score=_final_score(component_scores),
    )
    degraded_codes = [f"{name}_unavailable" for name in unavailable]
    summary = f"final_score={score.final_score:.2f}"
    if unavailable:
        summary = f"{summary}; neutral components: {', '.join(unavailable)}"
    return score, RankingExplanation(
        summary=summary,
        component_explanations=explanations,
        unavailable_data=unavailable,
        degraded_reason_codes=degraded_codes,
    )


class CandidateRankingEngine:
    def __init__(self, *, max_candidates: int = MAX_CANDIDATES_DEFAULT) -> None:
        if max_candidates <= 0:
            raise ValueError("max_candidates must be positive")
        self.max_candidates = max_candidates

    def rank(
        self,
        *,
        policy: UserPolicy,
        candidates: Sequence[CandidateUniverseItem],
        securities: Sequence[Mapping[str, Any]] | None = None,
        provider_metadata: Mapping[str, Mapping[str, Any]] | None = None,
        portfolio_snapshot: PortfolioSnapshot | None = None,
    ) -> list[RankedCandidate]:
        security_by_ticker = {
            _symbol(str(security.get("ticker", security.get("symbol", "")))): security
            for security in securities or []
            if str(security.get("ticker", security.get("symbol", ""))).strip()
        }
        scored: list[RankedCandidate] = []

        for candidate in candidates:
            ticker = _symbol(candidate.ticker)
            metadata = _merged_metadata(
                security_by_ticker.get(ticker, {}),
                _ticker_metadata(ticker, provider_metadata),
            )
            score, explanation = _candidate_score(
                policy=policy,
                candidate=candidate,
                metadata=metadata,
                snapshot=portfolio_snapshot,
            )
            scored.append(
                RankedCandidate(
                    candidate=candidate,
                    score=score,
                    explanation=explanation,
                    score_rank=0,
                    exclusion_reason=candidate.block_reason,
                )
            )

        scored.sort(key=lambda item: (-item.score.final_score, item.candidate.ticker))
        selected_count = 0
        ranked: list[RankedCandidate] = []
        for score_rank, item in enumerate(scored, start=1):
            selected = item.exclusion_reason is None and selected_count < self.max_candidates
            selected_rank = None
            exclusion_reason = item.exclusion_reason
            if selected:
                selected_count += 1
                selected_rank = selected_count
            elif exclusion_reason is None:
                exclusion_reason = "candidate_cap_exceeded"
            ranked.append(
                item.model_copy(
                    update={
                        "score_rank": score_rank,
                        "selected": selected,
                        "selected_rank": selected_rank,
                        "exclusion_reason": exclusion_reason,
                    }
                )
            )
        return ranked
