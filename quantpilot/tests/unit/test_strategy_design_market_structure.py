from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from random import Random
from statistics import mean, pstdev

import pytest
from pydantic import ValidationError

from quantpilot.services.research_agents.models import InvestorFlows, SectorMove
from quantpilot.services.strategy_design.analytics.market_structure import (
    build_market_structure,
    evidence_to_json,
    load_ohlcv_rows,
    load_securities,
)
from quantpilot.services.strategy_design.models import MarketStructureEvidence, VolatilityRegime

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LOCAL_DATA = _REPO_ROOT / "local_data"

N_DAYS = 300


def _business_days(n: int, start: date = date(2024, 1, 1)) -> list[str]:
    out: list[str] = []
    day = start
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _synthetic_closes(n: int = N_DAYS) -> dict[str, list[float]]:
    """A trends up, B trends down, C is flat with noise, D = 2 * A (correlation exactly 1)."""

    rng = Random(0)
    a: list[float] = []
    b: list[float] = []
    c: list[float] = []
    for i in range(n):
        a.append(100.0 * (1.003**i) * (1.0 + rng.uniform(-0.002, 0.002)))
        b.append(100.0 * (0.997**i) * (1.0 + rng.uniform(-0.002, 0.002)))
        c.append(100.0 + rng.uniform(-1.0, 1.0))
    return {"A": a, "B": b, "C": c, "D": [2.0 * x for x in a]}


def _rows_from_closes(closes: dict[str, list[float]], dates: list[str] | None = None) -> list[dict]:
    n = len(next(iter(closes.values())))
    dates = dates or _business_days(n)
    rows: list[dict] = []
    for symbol, series in closes.items():
        for d, close in zip(dates, series):
            rows.append(
                {
                    "symbol": symbol,
                    "date": d,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 1000,
                }
            )
    return rows


def _flow_history() -> list[InvestorFlows]:
    """60 trailing entries plus one latest spike: foreign alternates 100/200 (mean 150, pstdev 50)."""

    history = [
        InvestorFlows(foreign=100.0 if i % 2 == 0 else 200.0, institution=10.0, individual=float(i))
        for i in range(60)
    ]
    history.append(InvestorFlows(foreign=400.0, institution=10.0, individual=30.0))
    return history


@pytest.fixture(scope="module")
def synthetic() -> tuple[dict[str, list[float]], list[dict]]:
    closes = _synthetic_closes()
    return closes, _rows_from_closes(closes)


@pytest.fixture(scope="module")
def evidence(synthetic) -> MarketStructureEvidence:
    _, rows = synthetic
    return build_market_structure(
        rows,
        sector_moves=[SectorMove(name="자동차", change_pct=1.0), SectorMove(name="반도체", change_pct=3.0), SectorMove(name="은행", change_pct=-2.0)],
        flow_history=_flow_history(),
    )


def test_breadth_counts_match_sma_definition(synthetic, evidence: MarketStructureEvidence) -> None:
    closes, _ = synthetic
    assert evidence.universe_size == 4
    assert evidence.as_of == _business_days(N_DAYS)[-1]
    assert evidence.date_range == (_business_days(N_DAYS)[0], _business_days(N_DAYS)[-1])
    by_window = {p.window: p for p in evidence.breadth}
    assert set(by_window) == {20, 60, 120}
    for window, point in by_window.items():
        expected_above = {s for s, c in closes.items() if c[-1] > mean(c[-window:])}
        assert {"A", "D"} <= expected_above
        assert "B" not in expected_above
        assert point.count_total == 4
        assert point.count_above == len(expected_above)
        assert point.pct_above_sma == pytest.approx(len(expected_above) / 4 * 100.0)


def test_correlation_pairs_and_labels(evidence: MarketStructureEvidence) -> None:
    corr = evidence.correlation
    assert corr is not None
    assert corr.window == 60
    assert corr.n_symbols == 4
    assert corr.max_pairwise == pytest.approx(1.0, abs=1e-6)  # A vs D
    assert -1.0 <= corr.min_pairwise <= corr.mean_pairwise <= corr.max_pairwise <= 1.0
    assert corr.label in {"dispersed", "normal", "crowded"}


def test_volatility_regime_is_labelled(evidence: MarketStructureEvidence) -> None:
    vol = evidence.volatility
    assert vol.window == 20
    assert vol.realized_vol_annualized > 0.0
    assert vol.percentile_1y is not None and 0.0 <= vol.percentile_1y <= 100.0
    assert vol.label in {"low", "normal", "high", "extreme"}


def test_flow_z_scores(evidence: MarketStructureEvidence) -> None:
    flows = {f.group: f for f in evidence.flows}
    assert set(flows) == {"foreign", "institution", "individual"}
    foreign = flows["foreign"]
    assert foreign.latest == 400.0
    assert foreign.mean_60d == pytest.approx(150.0)
    assert foreign.stdev_60d == pytest.approx(50.0)
    assert foreign.z == pytest.approx((400.0 - 150.0) / 50.0)  # 5.0
    # constant history has zero spread -> z is undefined, not an exception
    assert flows["institution"].z is None
    trailing = [float(i) for i in range(60)]
    assert flows["individual"].z == pytest.approx((30.0 - mean(trailing)) / pstdev(trailing), abs=1e-6)


def test_sector_momentum_ranks_descending(evidence: MarketStructureEvidence) -> None:
    ranked = [(s.rank, s.name, s.change_pct) for s in evidence.sector_momentum]
    assert ranked == [(1, "반도체", 3.0), (2, "자동차", 1.0), (3, "은행", -2.0)]


def test_symbol_table(evidence: MarketStructureEvidence) -> None:
    table = {s.symbol: s for s in evidence.symbols}
    assert list(table) == ["A", "B", "C", "D"]
    for s in table.values():
        assert s.distance_from_high_252d_pct is not None and s.distance_from_high_252d_pct <= 0.0
        assert s.realized_vol_20d is not None and s.realized_vol_20d >= 0.0
        assert s.ret_120d is not None
    assert table["A"].ret_120d > 0 and table["A"].above_sma120 is True
    assert table["B"].ret_120d < 0 and table["B"].above_sma120 is False
    assert table["D"].ret_60d == pytest.approx(table["A"].ret_60d, abs=1e-6)


def test_as_of_ignores_later_rows(synthetic) -> None:
    closes, rows = synthetic
    dates = _business_days(N_DAYS)
    cutoff = dates[199]
    truncated = _rows_from_closes({s: c[:200] for s, c in closes.items()}, dates[:200])
    from_full = build_market_structure(rows, as_of=cutoff)
    from_truncated = build_market_structure(truncated)
    assert from_full.as_of == cutoff
    assert from_full.date_range[1] == cutoff
    assert from_full.model_dump() == from_truncated.model_dump()
    # and the later bars did change the answer, so the cut actually mattered
    assert from_full.symbols[0].close != build_market_structure(rows).symbols[0].close


def test_short_history_yields_none_and_notes(synthetic) -> None:
    closes, _ = synthetic
    rows = _rows_from_closes({s: c[:30] for s, c in closes.items()})
    short = build_market_structure(rows)
    assert short.universe_size == 4
    by_window = {p.window: p for p in short.breadth}
    assert by_window[20].count_total == 4
    assert by_window[60].count_total == 0 and by_window[60].pct_above_sma == 0.0
    assert by_window[120].count_total == 0
    assert short.correlation is None
    assert short.volatility.percentile_1y is None and short.volatility.label == "normal"
    for s in short.symbols:
        assert s.ret_60d is None and s.ret_120d is None
        assert s.above_sma60 is None and s.above_sma120 is None
        assert s.distance_from_high_252d_pct is None
        assert s.ret_20d is not None and s.above_sma20 is not None
    assert short.flows == []
    assert short.notes, "short history must be explained in notes"
    assert any("상관행렬" in n for n in short.notes)
    assert any("변동성" in n for n in short.notes)


def test_short_flow_history_sets_z_none() -> None:
    rows = _rows_from_closes(_synthetic_closes(30))
    short = build_market_structure(rows, flow_history=_flow_history()[:10])
    assert {f.group for f in short.flows} == {"foreign", "institution", "individual"}
    assert all(f.z is None for f in short.flows)
    assert any("수급" in n for n in short.notes)


def test_signal_input_cannot_be_true(evidence: MarketStructureEvidence) -> None:
    assert evidence.signal_input is False
    payload = evidence.model_dump()
    payload["signal_input"] = True
    with pytest.raises(ValidationError):
        MarketStructureEvidence.model_validate(payload)
    with pytest.raises(ValidationError):
        VolatilityRegime(window=20, realized_vol_annualized=1.0, percentile_1y=None, label="wild")


def test_evidence_json_round_trip(evidence: MarketStructureEvidence) -> None:
    text = evidence_to_json(evidence)
    assert "반도체" in text  # ensure_ascii=False keeps Korean readable for the agents
    parsed = json.loads(text)
    assert parsed["signal_input"] is False
    restored = MarketStructureEvidence.model_validate_json(text)
    assert restored == evidence


def test_load_ohlcv_rows_skips_malformed(tmp_path: Path) -> None:
    csv_path = tmp_path / "ohlcv.csv"
    csv_path.write_text(
        "symbol,date,open,high,low,close,volume\n"
        "A,2024-01-02,1,2,0.5,1.5,10\n"
        "A,not-a-date,1,2,0.5,1.5,10\n"
        "A,2024-01-03,1,2,0.5,abc,10\n"
        "A,2024-01-04,1,2,0.5,1.6,11\n",
        encoding="utf-8",
    )
    notes: list[str] = []
    rows = load_ohlcv_rows(csv_path, notes)
    assert [r["date"] for r in rows] == ["2024-01-02", "2024-01-04"]
    assert rows[0]["volume"] == 10 and isinstance(rows[0]["close"], float)
    assert notes and "2행" in notes[0]


@pytest.mark.skipif(
    not ((_LOCAL_DATA / "ohlcv.csv").exists() and (_LOCAL_DATA / "securities.csv").exists()),
    reason="local_data fixtures missing",
)
def test_real_local_data_smoke() -> None:
    rows = load_ohlcv_rows(_LOCAL_DATA / "ohlcv.csv")
    securities = load_securities(_LOCAL_DATA / "securities.csv")
    ev = build_market_structure(rows, securities=securities)
    assert ev.universe_size == 15
    assert ev.as_of == "2026-07-03"
    assert ev.correlation is not None and ev.correlation.n_symbols == 15
    assert all(s.name and s.sector for s in ev.symbols)
    assert all(s.distance_from_high_252d_pct is not None and s.distance_from_high_252d_pct <= 0.0 for s in ev.symbols)
    assert ev.signal_input is False
