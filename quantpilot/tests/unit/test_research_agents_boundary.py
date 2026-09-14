from __future__ import annotations

import yaml

from pathlib import Path

import pytest
from pydantic import ValidationError

from quantpilot.services.research_agents.models import (
    EvidenceBundle,
    InvestorFlows,
    MarketSnapshot,
)

_SERVICES_DIR = Path(__file__).resolve().parents[2] / "services"
_RESEARCH_DIR = _SERVICES_DIR / "research_agents"
# Every research-only package that must stay isolated from trading code.
_RESEARCH_PACKAGE_DIRS = (_RESEARCH_DIR, _SERVICES_DIR / "strategy_design")

# Mirrors tach.toml `cannot_depend_on` plus the sole POST authority module name,
# so the boundary holds even where tach is not installed (design: the research
# packages are never a trading input — acceptance matrix §1 "Research isolation").
_FORBIDDEN_IMPORTS = (
    "core.execution",
    "core.operator",
    "core.signals",
    "core.portfolio",
    "core.risk",
    "packages.brokers",
    "harness_service",
    "services.api",
    "paper_submission",
)


def _snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        date="2026-09-03",
        kospi_close=3000.0,
        kospi_change_pct=0.5,
        kosdaq_close=900.0,
        kosdaq_change_pct=-0.2,
        investor_flows=InvestorFlows(foreign=1200.0, institution=-300.0, individual=-900.0),
    )


@pytest.mark.parametrize("package_dir", _RESEARCH_PACKAGE_DIRS, ids=lambda p: p.name)
def test_research_boundary_never_imports_trading_code(package_dir: Path) -> None:
    sources = list(package_dir.rglob("*.py"))
    assert sources, f"{package_dir.name} package must contain python sources"
    for source_file in sources:
        text = source_file.read_text(encoding="utf-8")
        for forbidden in _FORBIDDEN_IMPORTS:
            assert forbidden not in text, (
                f"{source_file.relative_to(package_dir)} references '{forbidden}' — "
                "the research boundary must stay isolated from trading code"
            )


def test_evidence_bundle_can_never_be_marked_as_signal_input() -> None:
    bundle = EvidenceBundle(date="2026-09-03", collected_at="2026-09-03T16:10:00+09:00", snapshot=_snapshot())
    assert bundle.signal_input is False
    with pytest.raises(ValidationError):
        EvidenceBundle(
            date="2026-09-03",
            collected_at="2026-09-03T16:10:00+09:00",
            snapshot=_snapshot(),
            signal_input=True,
        )


def test_tach_contract_lists_every_forbidden_module_for_both_research_packages() -> None:
    contract = (Path(__file__).resolve().parents[3] / "tach.toml").read_text(encoding="utf-8")
    for module in (
        "quantpilot.packages.core.execution",
        "quantpilot.packages.core.operator",
        "quantpilot.packages.core.signals",
        "quantpilot.packages.core.portfolio",
        "quantpilot.packages.core.risk",
        "quantpilot.packages.brokers",
        "quantpilot.packages.core.harness_service",
        "quantpilot.services.api",
    ):
        # once per research package block, plus once as an unchecked declaration
        assert contract.count(f'"{module}"') >= 3, module
        assert f'path = "{module}"' in contract, module
    assert 'path = "quantpilot.services.research_agents"' in contract
    assert 'path = "quantpilot.services.briefing"' in contract
    assert 'path = "quantpilot.services.strategy_design"' in contract


def test_designer_authored_recipes_stay_draft_without_execution_levels() -> None:
    """A hand edit of a draft recipe must not quietly earn execution levels (security gate finding SG-02)."""

    specs = Path(__file__).resolve().parents[3] / "quantpilot" / "docs" / "strategy_specs"
    for path in sorted(specs.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if (data.get("audit_metadata") or {}).get("authored_by") != "qp-design-strategy-designer":
            continue
        assert data.get("promotion_status") == "draft", path.name
        assert not data.get("allowed_execution_levels"), path.name
        assert (data.get("execution_permissions") or {}).get("market_orders") == "disabled", path.name
