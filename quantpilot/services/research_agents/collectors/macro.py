"""Macro evidence for the strategist team: ECOS + FRED + GPR → `MacroEvidence`.

Keys are read from the environment only (`ECOS_API_KEY`, `FRED_API_KEY`).
A missing key or a failed source is *skipped and named* in `skipped`, never
faked: the regime call then comes back `undetermined` and the agents say so.
Series codes live in `config/macro_series.json`; codes marked
`verified: false` are carried into the evidence so the analyst can flag them.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from quantpilot.services.research_agents.analytics.macro_regime import classify_regime, enrich_series, percentile_rank
from quantpilot.services.research_agents.collectors.ecos import ECOS_KEY_ENV, EcosClient, MacroCollectionError
from quantpilot.services.research_agents.collectors.fred import FRED_KEY_ENV, FredClient
from quantpilot.services.research_agents.collectors.gdelt import GDELT_CITATION, GdeltClient, collect_gdelt_themes
from quantpilot.services.research_agents.collectors.gpr import load_gpr
from quantpilot.services.research_agents.collectors.manifold import MANIFOLD_CITATION, ManifoldClient, collect_markets
from quantpilot.services.research_agents.models import (
    EvidenceSource,
    GprSummary,
    MacroEvidence,
    MacroPoint,
    MacroSeries,
)

log = logging.getLogger("quantpilot.macro")

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "macro_series.json"
_MONTHLY_YEARS = 11  # 60-month window + 12-month YoY + slack
_DAILY_YEARS = 6

EcosFactory = Callable[[str], EcosClient]
FredFactory = Callable[[str], FredClient]
GprLoader = Callable[..., tuple[list[dict[str, Any]] | None, str]]
GdeltFactory = Callable[[], GdeltClient]
ManifoldFactory = Callable[[], ManifoldClient]


def load_macro_config(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or DEFAULT_CONFIG_PATH).read_text(encoding="utf-8"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _ecos_window(cycle: str, as_of: date) -> tuple[str, str]:
    if cycle == "D":
        start = as_of - timedelta(days=365 * _DAILY_YEARS)
        return start.strftime("%Y%m%d"), as_of.strftime("%Y%m%d")
    start = as_of.replace(day=1) - timedelta(days=365 * _MONTHLY_YEARS)
    if cycle == "Q":
        return f"{start.year}Q1", f"{as_of.year}Q{(as_of.month - 1) // 3 + 1}"
    if cycle == "A":
        return str(start.year), str(as_of.year)
    return start.strftime("%Y%m"), as_of.strftime("%Y%m")


def _collect_ecos(entries: list[dict[str, Any]], client: EcosClient, as_of: date, skipped: list[str]) -> list[MacroSeries]:
    out: list[MacroSeries] = []
    for entry in entries:
        cycle = str(entry.get("cycle", "M"))
        start, end = _ecos_window(cycle, as_of)
        try:
            points = client.series(str(entry["stat_code"]), cycle, start, end, str(entry["item_code"]))
        except MacroCollectionError as exc:
            skipped.append(f"ecos:{entry['id']}: {exc}")
            continue
        out.append(
            enrich_series(
                MacroSeries(
                    id=str(entry["id"]),
                    label=str(entry.get("label", entry["id"])),
                    source="ecos",
                    code=f"{entry['stat_code']}/{entry['item_code']}",
                    cycle=cycle,  # type: ignore[arg-type]
                    unit=str(entry.get("unit", "")),
                    role=str(entry.get("role", "")),
                    verified=bool(entry.get("verified", True)),
                    points=points,
                    note="" if points else "empty answer from ECOS (check the code with --list-items)",
                )
            )
        )
    return out


def _collect_fred(entries: list[dict[str, Any]], client: FredClient, as_of: date, skipped: list[str]) -> list[MacroSeries]:
    out: list[MacroSeries] = []
    start = (as_of - timedelta(days=365 * _MONTHLY_YEARS)).isoformat()
    for entry in entries:
        try:
            points = client.observations(str(entry["series_id"]), start=start)
        except MacroCollectionError as exc:
            skipped.append(f"fred:{entry['id']}: {exc}")
            continue
        cycle = "D" if _looks_daily(points) else "M"
        out.append(
            enrich_series(
                MacroSeries(
                    id=str(entry["id"]),
                    label=str(entry.get("label", entry["id"])),
                    source="fred",
                    code=str(entry["series_id"]),
                    cycle=cycle,
                    unit=str(entry.get("unit", "")),
                    role=str(entry.get("role", "")),
                    points=points,
                )
            )
        )
    return out


def _looks_daily(points: list[MacroPoint]) -> bool:
    if len(points) < 40:
        return False
    first, last = points[-40].time, points[-1].time
    try:
        span = (date.fromisoformat(last) - date.fromisoformat(first)).days
    except ValueError:
        return False
    return span < 120


def _gpr_summary(rows: list[dict[str, Any]], citation: str) -> GprSummary | None:
    if not rows:
        return None
    latest = rows[-1]
    trailing = [r["gpr"] for r in rows[-120:] if r.get("gpr") is not None]
    korea_vals = [r["gpr_korea"] for r in rows[-120:] if r.get("gpr_korea") is not None]
    three = rows[-4] if len(rows) >= 4 else None
    return GprSummary(
        as_of_month=str(latest["month"]),
        gpr=round(float(latest["gpr"]), 3),
        gpr_percentile_10y=percentile_rank(trailing, float(latest["gpr"])),
        gpr_change_3m=round(float(latest["gpr"]) - float(three["gpr"]), 3) if three and three.get("gpr") is not None else None,
        gpr_threats=latest.get("gpr_threats"),
        gpr_acts=latest.get("gpr_acts"),
        gpr_korea=latest.get("gpr_korea"),
        gpr_korea_percentile_10y=percentile_rank(korea_vals, float(latest["gpr_korea"])) if latest.get("gpr_korea") is not None else None,
        citation=citation,
    )


def collect_macro(
    *,
    as_of: date,
    repo_root: Path,
    config: dict[str, Any] | None = None,
    environ: dict[str, str] | None = None,
    ecos_factory: EcosFactory = EcosClient,
    fred_factory: FredFactory = FredClient,
    gpr_loader: GprLoader = load_gpr,
    gdelt_factory: GdeltFactory = GdeltClient,
    manifold_factory: ManifoldFactory = ManifoldClient,
    collected_at: str | None = None,
) -> MacroEvidence:
    cfg = config or load_macro_config()
    env = os.environ if environ is None else environ
    stamp = collected_at or _now_iso()
    skipped: list[str] = []
    series: list[MacroSeries] = []
    sources: list[EvidenceSource] = []

    ecos_key = env.get(ECOS_KEY_ENV, "")
    if ecos_key:
        try:
            series += _collect_ecos(cfg.get("ecos", []), ecos_factory(ecos_key), as_of, skipped)
            sources.append(EvidenceSource(id="bok_ecos", fetched_at=stamp, detail="Bank of Korea ECOS StatisticSearch"))
        except MacroCollectionError as exc:
            skipped.append(f"ecos: {exc}")
    else:
        skipped.append(f"ecos: {ECOS_KEY_ENV} not set")

    fred_key = env.get(FRED_KEY_ENV, "")
    if fred_key:
        try:
            series += _collect_fred(cfg.get("fred", []), fred_factory(fred_key), as_of, skipped)
            sources.append(EvidenceSource(id="fred", fetched_at=stamp, detail="FRED series/observations"))
        except MacroCollectionError as exc:
            skipped.append(f"fred: {exc}")
    else:
        skipped.append(f"fred: {FRED_KEY_ENV} not set")

    gpr_cfg = cfg.get("gpr", {})
    gpr: GprSummary | None = None
    if gpr_cfg:
        rows, note = gpr_loader(
            url=str(gpr_cfg.get("url", "")),
            csv_fallback=str(gpr_cfg.get("csv_fallback", "local_data/gpr.csv")),
            columns=dict(gpr_cfg.get("columns", {})),
            repo_root=repo_root,
        )
        if rows is None:
            skipped.append(note)
        else:
            gpr = _gpr_summary(rows, str(gpr_cfg.get("citation", "")))
            sources.append(EvidenceSource(id="gpr_index", fetched_at=stamp, detail=note))

    gdelt_cfg = cfg.get("gdelt", {})
    gdelt_themes = []
    if gdelt_cfg.get("themes"):
        try:
            gdelt_themes = collect_gdelt_themes(
                gdelt_factory(),
                list(gdelt_cfg["themes"]),
                timespan_volume=str(gdelt_cfg.get("timespan_volume", "30d")),
                timespan_articles=str(gdelt_cfg.get("timespan_articles", "7d")),
                max_records=int(gdelt_cfg.get("max_records", 5)),
            )
            if gdelt_themes:
                sources.append(EvidenceSource(id="gdelt_doc", fetched_at=stamp, detail=f"{GDELT_CITATION}; {len(gdelt_themes)} themes"))
            for theme in gdelt_themes:
                if theme.note:
                    skipped.append(f"gdelt:{theme.id}: {theme.note}")
        except Exception as exc:  # the client already narrows per theme; this guards the factory itself
            skipped.append(f"gdelt: {type(exc).__name__}")

    manifold_cfg = cfg.get("manifold", {})
    markets = []
    if manifold_cfg.get("terms"):
        try:
            markets, notes = collect_markets(manifold_factory(), list(manifold_cfg["terms"]), limit=int(manifold_cfg.get("limit", 5)))
            skipped.extend(notes)
            if markets:
                sources.append(EvidenceSource(id="manifold", fetched_at=stamp, detail=f"{MANIFOLD_CITATION}; {len(markets)} open binary markets"))
        except Exception as exc:
            skipped.append(f"manifold: {type(exc).__name__}")

    regime = classify_regime({s.id: s for s in series}, cfg.get("regime", {}))
    for item in skipped:
        log.warning("macro skipped: %s", item)
    return MacroEvidence(as_of=as_of.isoformat(), collected_at=stamp, series=series, regime=regime, gpr=gpr, gdelt=gdelt_themes, markets=markets, skipped=skipped, sources=sources)
