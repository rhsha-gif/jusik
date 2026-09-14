from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from quantpilot.services.research_agents.analytics.macro_regime import (
    classify_regime,
    enrich_series,
    percentile_rank,
    yoy_series,
    zscore,
)
from quantpilot.services.research_agents.collectors.ecos import EcosClient, MacroCollectionError, ecos_time_to_iso
from quantpilot.services.research_agents.collectors.fred import FredClient
from quantpilot.services.research_agents.collectors.gpr import load_gpr, parse_gpr_csv
from quantpilot.services.research_agents.collectors.macro import collect_macro, load_macro_config
from quantpilot.services.research_agents.models import GdeltArticle, MacroEvidence, MacroPoint, MacroSeries, PredictionMarket
from quantpilot.services.research_agents.runner import agent_environment

KEY = "FAKE-SAMPLEKEY-1234567890"


def _monthly(n: int, start_year: int = 2016, fn=lambda i: 100.0 + i) -> list[MacroPoint]:
    out = []
    for i in range(n):
        year, month = divmod(i, 12)
        out.append(MacroPoint(time=f"{start_year + year:04d}-{month + 1:02d}", value=fn(i)))
    return out


# --- ECOS ----------------------------------------------------------------------


def _ecos_payload(rows: list[dict[str, str]]) -> bytes:
    return json.dumps({"StatisticSearch": {"list_total_count": len(rows), "row": rows}}).encode("utf-8")


def test_ecos_series_parses_rows_and_never_leaks_the_key_in_errors() -> None:
    seen: list[str] = []

    def fetch(url: str) -> bytes:
        seen.append(url)
        return _ecos_payload([{"TIME": "202601", "DATA_VALUE": "3.00"}, {"TIME": "202512", "DATA_VALUE": "3.25"}])

    client = EcosClient(KEY, fetch=fetch)
    points = client.series("722Y001", "M", "202501", "202601", "0101000")
    assert [p.time for p in points] == ["2025-12", "2026-01"] and points[-1].value == 3.0
    assert KEY in seen[0] and "/json/kr/1/5000/722Y001/M/202501/202601/0101000" in seen[0]

    def failing(url: str) -> bytes:
        return json.dumps({"RESULT": {"CODE": "ERROR-100", "MESSAGE": f"인증키 오류 {KEY}"}}).encode("utf-8")

    with pytest.raises(MacroCollectionError) as excinfo:
        EcosClient(KEY, fetch=failing).series("722Y001", "M", "202501", "202601", "0101000")
    assert KEY not in str(excinfo.value) and "ERROR-100" in str(excinfo.value)


def test_ecos_no_data_is_an_empty_list_and_time_conversion() -> None:
    def fetch(url: str) -> bytes:
        return json.dumps({"RESULT": {"CODE": "INFO-200", "MESSAGE": "no data"}}).encode("utf-8")

    assert EcosClient(KEY, fetch=fetch).series("817Y002", "D", "20260101", "20260131", "010200000") == []
    assert ecos_time_to_iso("20260912", "D") == "2026-09-12"
    assert ecos_time_to_iso("202609", "M") == "2026-09"
    assert ecos_time_to_iso("2026Q1", "Q") == "2026Q1"


# --- FRED ----------------------------------------------------------------------


def test_fred_drops_missing_values_and_redacts_key() -> None:
    def fetch(url: str) -> bytes:
        assert f"api_key={KEY}" in url
        return json.dumps({"observations": [{"date": "2026-01-02", "value": "4.1"}, {"date": "2026-01-05", "value": "."}]}).encode("utf-8")

    points = FredClient(KEY, fetch=fetch).observations("DGS10", start="2020-01-01")
    assert [(p.time, p.value) for p in points] == [("2026-01-02", 4.1)]

    def bad(url: str) -> bytes:
        return json.dumps({"error_code": 400, "error_message": f"Bad Request. api_key {KEY} invalid"}).encode("utf-8")

    with pytest.raises(MacroCollectionError) as excinfo:
        FredClient(KEY, fetch=bad).observations("DGS10", start="2020-01-01")
    assert KEY not in str(excinfo.value)


# --- GPR -----------------------------------------------------------------------

_GPR_COLUMNS = {"month": "month", "gpr": "GPR", "gpr_threats": "GPRT", "gpr_acts": "GPRA", "gpr_korea": "GPRC_KOR"}


def test_gpr_csv_fallback_wins_over_download(tmp_path: Path) -> None:
    (tmp_path / "local_data").mkdir()
    (tmp_path / "local_data" / "gpr.csv").write_text(
        "month,GPR,GPRT,GPRA,GPRC_KOR\n2026-06-01,120.5,130.1,110.2,0.8\n2026-07-01,140.0,150.0,120.0,1.1\n", encoding="utf-8"
    )
    calls: list[str] = []
    rows, note = load_gpr(url="https://example.invalid/x.xls", csv_fallback="local_data/gpr.csv", columns=_GPR_COLUMNS, repo_root=tmp_path, fetch=lambda u: calls.append(u) or b"")
    assert calls == [] and "local csv" in note
    assert rows is not None and rows[-1]["month"] == "2026-07" and rows[-1]["gpr_korea"] == 1.1


def test_gpr_without_xlrd_or_csv_is_skipped_not_raised(tmp_path: Path) -> None:
    rows, note = load_gpr(url="https://example.invalid/x.xls", csv_fallback="local_data/gpr.csv", columns=_GPR_COLUMNS, repo_root=tmp_path, fetch=lambda u: b"not-a-workbook")
    assert rows is None and "gpr" in note


def test_gpr_refuses_unexpected_hosts(tmp_path: Path) -> None:
    calls: list[str] = []
    rows, note = load_gpr(url="https://evil.example/x.xls", csv_fallback="local_data/gpr.csv", columns=_GPR_COLUMNS, repo_root=tmp_path, fetch=lambda u: calls.append(u) or b"")
    assert rows is None and calls == [] and "unexpected host" in note


def test_parse_gpr_csv_requires_month_and_gpr_columns() -> None:
    with pytest.raises(MacroCollectionError):
        parse_gpr_csv("date,value\n2026-01,1\n", _GPR_COLUMNS)


# --- analytics -----------------------------------------------------------------


def test_enrich_series_derives_stats_only_from_points() -> None:
    s = enrich_series(MacroSeries(id="kr_cpi", label="CPI", source="ecos", code="901Y009/0", cycle="M", role="inflation", points=_monthly(72)))
    assert s.latest == 171.0 and s.latest_time == "2021-12"
    assert s.change_3m == 3.0
    assert s.yoy_pct == pytest.approx(100 * (171 / 159 - 1), abs=0.01)
    assert s.percentile_5y is not None and s.percentile_5y > 95
    assert s.zscore_24m is not None and s.zscore_24m > 1.5
    rate = enrich_series(MacroSeries(id="r", label="r", source="fred", code="DGS10", cycle="D", role="rates", points=[MacroPoint(time=f"2026-01-{i + 1:02d}", value=4.0) for i in range(10)]))
    assert rate.yoy_pct is None and rate.zscore_24m is None and rate.latest == 4.0


def test_enrich_series_empty_points_is_a_note_not_an_error() -> None:
    s = enrich_series(MacroSeries(id="x", label="x", source="ecos", code="c", points=[]))
    assert s.latest is None and "no data" in s.note


def test_yoy_zscore_percentile_helpers() -> None:
    yoy = yoy_series(_monthly(24), "M")
    assert len(yoy) == 12 and yoy[0].value == pytest.approx(12.0)
    assert zscore([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], 6.0) == pytest.approx((6 - 3.5) / 1.707825, abs=1e-4)
    assert zscore([1.0, 1.0, 1.0, 1.0, 1.0, 1.0], 1.0) is None
    assert percentile_rank([1, 2, 3, 4], 4) == 87.5


def test_classify_regime_quadrants_and_undetermined() -> None:
    cfg = {"growth_ids": ["g"], "inflation_ids": ["i"], "zscore_short_months": 24, "zscore_long_months": 60, "short_weight": 0.6}
    accelerating = MacroSeries(id="g", label="g", source="fred", code="INDPRO", cycle="M", role="growth", points=_monthly(84, fn=lambda i: 100 * (1.002**i) * (1.03 if i >= 80 else 1.0)))
    decelerating = MacroSeries(id="i", label="i", source="ecos", code="901Y009/0", cycle="M", role="inflation", points=_monthly(84, fn=lambda i: 100 * (1.003**i) * (0.97 if i >= 80 else 1.0)))
    call = classify_regime({"g": accelerating, "i": decelerating}, cfg)
    assert call.quadrant == "Q1_growth_up_inflation_down" and call.growth_score > 0 > call.inflation_score
    assert call.growth_inputs == ["g"] and call.inflation_inputs == ["i"]
    missing = classify_regime({"g": accelerating}, cfg)
    assert missing.quadrant == "undetermined" and "inflation" in missing.note


# --- orchestrator ---------------------------------------------------------------


class _FakeEcos:
    def __init__(self, key: str) -> None:
        assert key == KEY

    def series(self, stat_code: str, cycle: str, start: str, end: str, item_code: str) -> list[MacroPoint]:
        if stat_code == "901Y118":
            raise MacroCollectionError("ecos 901Y118/T002: ERROR-101")
        return _monthly(84) if cycle == "M" else [MacroPoint(time=f"2026-01-{i + 1:02d}", value=3.0 + i / 100) for i in range(20)]


class _FakeGdelt:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    def attention(self, query: str, *, timespan: str = "30d"):
        if self._fail:
            raise ValueError("gdelt: non-JSON answer")
        return 0.4, 0.2, 30

    def articles(self, query: str, *, timespan: str = "7d", max_records: int = 5):
        return [GdeltArticle(id="gdelt:abc1234567", title="t", url="https://example.org/a", domain="example.org", source_country="South Korea", language="Korean", seen_at="2026-09-10T00:00:00Z")]


class _FakeManifold:
    def search(self, term: str, *, limit: int = 5):
        return [PredictionMarket(id="manifold:m1", question=f"Will {term}?", probability=0.62, close_date="2026-12-31", url="https://manifold.markets/x", term=term)]


class _FakeFred:
    def __init__(self, key: str) -> None:
        assert key == KEY

    def observations(self, series_id: str, *, start: str) -> list[MacroPoint]:
        return _monthly(84, fn=lambda i: 100 * (1.002**i))


def test_collect_macro_skips_missing_keys_and_names_them(tmp_path: Path) -> None:
    ev = collect_macro(as_of=date(2026, 9, 14), repo_root=tmp_path, environ={}, gpr_loader=lambda **kw: (None, "gpr: download failed (URLError)"), gdelt_factory=lambda: _FakeGdelt(fail=True), manifold_factory=_FakeManifold)
    assert ev.series == [] and ev.regime is not None and ev.regime.quadrant == "undetermined"
    assert ev.gdelt and ev.gdelt[0].attention_ratio is None and any(s.startswith("gdelt:") for s in ev.skipped)
    assert any("ECOS_API_KEY" in s for s in ev.skipped) and any("FRED_API_KEY" in s for s in ev.skipped) and any(s.startswith("gpr") for s in ev.skipped)
    assert ev.signal_input is False
    with pytest.raises(Exception):
        MacroEvidence.model_validate({**ev.model_dump(), "signal_input": True})


def test_collect_macro_with_fakes_builds_series_regime_and_gpr(tmp_path: Path) -> None:
    cfg = load_macro_config()
    rows = [{"month": f"2020-{m:02d}" if m <= 12 else f"2021-{m - 12:02d}", "gpr": 100.0 + m, "gpr_korea": 0.5 + m / 100} for m in range(1, 20)]
    ev = collect_macro(
        as_of=date(2026, 9, 14),
        repo_root=tmp_path,
        config=cfg,
        environ={"ECOS_API_KEY": KEY, "FRED_API_KEY": KEY},
        ecos_factory=_FakeEcos,  # type: ignore[arg-type]
        fred_factory=_FakeFred,  # type: ignore[arg-type]
        gpr_loader=lambda **kw: (rows, "gpr: downloaded xls"),
        gdelt_factory=_FakeGdelt,
        manifold_factory=_FakeManifold,
    )
    ids = {s.id for s in ev.series}
    assert "kr_cpi" in ids and "us_cpi" in ids and "kr_exports" not in ids
    assert any("kr_exports" in s for s in ev.skipped)
    assert ev.regime is not None and ev.regime.quadrant != "undetermined"
    assert ev.gpr is not None and ev.gpr.as_of_month == "2021-07" and ev.gpr.gpr == 119.0 and ev.gpr.gpr_change_3m == 3.0
    assert {s.id for s in ev.sources} == {"bok_ecos", "fred", "gpr_index", "gdelt_doc", "manifold"}
    assert ev.gdelt and ev.gdelt[0].attention_ratio == 2.0 and ev.gdelt[0].articles[0].id.startswith("gdelt:")
    assert ev.markets and ev.markets[0].probability == 0.62 and len({m.id for m in ev.markets}) == len(ev.markets)
    dumped = json.dumps(ev.model_dump(), ensure_ascii=False)
    assert KEY not in dumped


def test_macro_config_is_well_formed() -> None:
    cfg = load_macro_config()
    for entry in cfg["ecos"]:
        assert {"id", "stat_code", "item_code", "cycle", "role"} <= set(entry)
    for entry in cfg["fred"]:
        assert {"id", "series_id", "role"} <= set(entry)
    assert set(cfg["regime"]["growth_ids"]) <= {e["id"] for e in cfg["ecos"] + cfg["fred"]}


def test_agent_process_never_sees_macro_keys() -> None:
    env = agent_environment({"ECOS_API_KEY": KEY, "FRED_API_KEY": KEY, "PATH": "x"})
    assert "ECOS_API_KEY" not in env and "FRED_API_KEY" not in env and env["PATH"] == "x"
