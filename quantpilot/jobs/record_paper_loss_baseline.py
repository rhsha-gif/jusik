"""Record one manually confirmed KIS paper loss baseline without network I/O."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from math import isfinite
from pathlib import Path
from zoneinfo import ZoneInfo

from quantpilot.packages.core.kis_paper import (
    paper_account_scope_fingerprint,
)
from quantpilot.packages.core.operator.position_ledger import (
    PaperPortfolioLossBaseline,
)
from quantpilot.packages.db.sqlite_repositories import PaperStateStore


KST = ZoneInfo("Asia/Seoul")
BASELINE_CONFIRMATION = "confirm paper loss baseline"


class PaperBaselineError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class PaperBaselineConfig:
    database_path: Path
    business_date: date
    source_business_date: date
    prior_close_equity: float = field(repr=False)
    month_start_equity: float = field(repr=False)
    account_number: str = field(repr=False)
    product_code: str = field(repr=False)

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "PaperBaselineConfig":
        env = environment or os.environ
        if env.get("KIS_PAPER_BASELINE_CONFIRMATION", "") != BASELINE_CONFIRMATION:
            raise PaperBaselineError("paper_loss_baseline_confirmation_required")
        if env.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
            raise PaperBaselineError("live_trading_flag_engaged")
        try:
            path = Path(_required(env, "KIS_PAPER_STATE_DB")).expanduser()
            business_date = date.fromisoformat(
                _required(env, "KIS_PAPER_APPROVED_BUSINESS_DATE")
            )
            source_date = date.fromisoformat(
                _required(env, "KIS_PAPER_BASELINE_SOURCE_DATE")
            )
            prior_close = float(
                _required(env, "KIS_PAPER_PRIOR_CLOSE_EQUITY")
            )
            month_start = float(
                _required(env, "KIS_PAPER_MONTH_START_EQUITY")
            )
            account = _required(env, "KIS_PAPER_ACCOUNT_NUMBER")
            product = _required(env, "KIS_PAPER_PRODUCT_CODE")
        except (TypeError, ValueError):
            raise PaperBaselineError("paper_loss_baseline_configuration_invalid") from None
        if (
            str(path) == ":memory:"
            or not path.is_absolute()
            or source_date >= business_date
            or prior_close <= 0
            or month_start <= 0
            or not isfinite(prior_close)
            or not isfinite(month_start)
        ):
            raise PaperBaselineError("paper_loss_baseline_configuration_invalid")
        return cls(
            database_path=path,
            business_date=business_date,
            source_business_date=source_date,
            prior_close_equity=prior_close,
            month_start_equity=month_start,
            account_number=account,
            product_code=product,
        )


def record_baseline(
    config: PaperBaselineConfig,
    *,
    confirmed_at: datetime,
) -> PaperPortfolioLossBaseline:
    if confirmed_at.tzinfo is None or confirmed_at.utcoffset() is None:
        raise PaperBaselineError("paper_loss_baseline_clock_invalid")
    if confirmed_at.astimezone(KST).date() != config.business_date:
        raise PaperBaselineError("paper_loss_baseline_date_not_current")
    fingerprint = paper_account_scope_fingerprint(
        config.account_number,
        config.product_code,
    )
    with PaperStateStore(
        config.database_path,
        data_mode="paper_trading",
        broker_environment="kis_paper",
        account_scope_fingerprint=fingerprint,
    ) as store:
        existing = store.load_paper_portfolio_loss_baseline(
            config.business_date
        )
        if existing is not None:
            if (
                existing.source == "manual_confirmed"
                and existing.source_business_date
                == config.source_business_date
                and existing.prior_close_equity
                == config.prior_close_equity
                and existing.month_start_equity
                == config.month_start_equity
            ):
                return existing
            raise PaperBaselineError("paper_loss_baseline_conflict")
        baseline = PaperPortfolioLossBaseline(
            store_id=store.provenance.store_id,
            account_scope_fingerprint=fingerprint,
            business_date=config.business_date,
            month_key=config.business_date.strftime("%Y-%m"),
            prior_close_equity=config.prior_close_equity,
            month_start_equity=config.month_start_equity,
            source="manual_confirmed",
            source_business_date=config.source_business_date,
            captured_at=confirmed_at,
            confirmed_at=confirmed_at,
        )
        return store.insert_paper_portfolio_loss_baseline(baseline)


def main() -> int:
    try:
        config = PaperBaselineConfig.from_environment()
        baseline = record_baseline(
            config,
            confirmed_at=datetime.now(tz=KST),
        )
    except PaperBaselineError as exc:
        print(json.dumps({"status": "blocked", "reason_code": exc.reason_code}))
        return 2
    except Exception:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "reason_code": "paper_loss_baseline_internal_failure",
                }
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": "recorded",
                "reason_code": "paper_loss_baseline_recorded",
                "business_date": baseline.business_date.isoformat(),
            }
        )
    )
    return 0


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise PaperBaselineError("paper_loss_baseline_configuration_incomplete")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
