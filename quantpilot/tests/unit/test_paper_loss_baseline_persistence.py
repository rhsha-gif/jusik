from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from quantpilot.packages.core.operator.position_ledger import (
    PaperPortfolioLossBaseline,
)
from quantpilot.packages.db.sqlite_repositories import (
    PAPER_STATE_SCHEMA_VERSION,
    PaperStateConflictError,
    PaperStateMigrationRequired,
    PaperStateProvenanceError,
    PaperStateStore,
)


KST = ZoneInfo("Asia/Seoul")
BUSINESS_DATE = date(2026, 7, 10)
CAPTURED_AT = datetime(2026, 7, 10, 9, 0, tzinfo=KST)
ACCOUNT_A = "sha256:" + "a" * 64
ACCOUNT_B = "sha256:" + "b" * 64


def _paper_store(path, *, account: str = ACCOUNT_A) -> PaperStateStore:
    return PaperStateStore(
        path,
        data_mode="paper_trading",
        broker_environment="kis_paper",
        account_scope_fingerprint=account,
    )


def _baseline(
    store: PaperStateStore,
    **updates: object,
) -> PaperPortfolioLossBaseline:
    values: dict[str, object] = {
        "store_id": store.provenance.store_id,
        "account_scope_fingerprint": ACCOUNT_A,
        "business_date": BUSINESS_DATE,
        "month_key": "2026-07",
        "prior_close_equity": 10_000_000.0,
        "month_start_equity": 9_800_000.0,
        "source": "manual_confirmed",
        "source_business_date": date(2026, 7, 9),
        "captured_at": CAPTURED_AT,
        "confirmed_at": CAPTURED_AT + timedelta(minutes=1),
    }
    values.update(updates)
    return PaperPortfolioLossBaseline(**values)


def test_first_ever_loss_baseline_is_missing_and_never_auto_zero(tmp_path) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        assert store.load_paper_portfolio_loss_baseline(BUSINESS_DATE) is None
        assert store.list_paper_portfolio_loss_baselines() == []


def test_loss_baseline_survives_restart(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    with _paper_store(path) as store:
        baseline = _baseline(store)
        assert store.insert_paper_portfolio_loss_baseline(baseline) == baseline

    with _paper_store(path) as reopened:
        assert (
            reopened.load_paper_portfolio_loss_baseline(BUSINESS_DATE)
            == baseline
        )
        assert reopened.list_paper_portfolio_loss_baselines() == [baseline]


def test_duplicate_is_idempotent_but_conflicting_evidence_is_rejected(tmp_path) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        baseline = _baseline(store)
        store.insert_paper_portfolio_loss_baseline(baseline)
        assert store.insert_paper_portfolio_loss_baseline(baseline) == baseline

        conflicting = baseline.model_copy(
            update={"prior_close_equity": 10_100_000.0}
        )
        with pytest.raises(PaperStateConflictError, match="different evidence"):
            store.insert_paper_portfolio_loss_baseline(
                PaperPortfolioLossBaseline.model_validate(
                    conflicting.model_dump()
                )
            )
        with pytest.raises(ValidationError):
            PaperPortfolioLossBaseline.model_validate(
                {**baseline.model_dump(), "revision": 1}
            )


def test_fixture_and_wrong_account_stores_fail_closed(tmp_path) -> None:
    paper_path = tmp_path / "paper.sqlite3"
    with _paper_store(paper_path) as paper:
        baseline = _baseline(paper)
        wrong_account = baseline.model_copy(
            update={"account_scope_fingerprint": ACCOUNT_B}
        )
        with pytest.raises(PaperStateProvenanceError, match="provenance"):
            paper.insert_paper_portfolio_loss_baseline(
                PaperPortfolioLossBaseline.model_validate(
                    wrong_account.model_dump()
                )
            )

    with pytest.raises(PaperStateProvenanceError, match="does not match"):
        _paper_store(paper_path, account=ACCOUNT_B)

    with PaperStateStore(tmp_path / "fixture.sqlite3") as fixture:
        with pytest.raises(PaperStateProvenanceError, match="KIS-paper"):
            fixture.load_paper_portfolio_loss_baseline(BUSINESS_DATE)
        with pytest.raises(PaperStateProvenanceError, match="KIS-paper"):
            fixture.insert_paper_portfolio_loss_baseline(baseline)


def test_populated_legacy_database_cannot_gain_paper_loss_baseline(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA user_version = 5")
        connection.execute("CREATE TABLE legacy_state (payload TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_state VALUES ('fixture')")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PaperStateMigrationRequired, match="cannot be promoted"):
        _paper_store(path)


def test_prior_session_chain_and_month_rollover_require_confirmation(tmp_path) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        first = _baseline(store)
        store.insert_paper_portfolio_loss_baseline(first)

        next_day = _baseline(
            store,
            business_date=date(2026, 7, 11),
            source="prior_session_close",
            source_business_date=BUSINESS_DATE,
            prior_close_equity=10_050_000.0,
            captured_at=datetime(2026, 7, 11, 9, 0, tzinfo=KST),
            confirmed_at=None,
        )
        assert store.insert_paper_portfolio_loss_baseline(next_day) == next_day

        changed_month_start = _baseline(
            store,
            business_date=date(2026, 7, 12),
            source="prior_session_close",
            source_business_date=date(2026, 7, 11),
            month_start_equity=9_900_000.0,
            captured_at=datetime(2026, 7, 12, 9, 0, tzinfo=KST),
            confirmed_at=None,
        )
        with pytest.raises(PaperStateConflictError, match="month-start"):
            store.insert_paper_portfolio_loss_baseline(changed_month_start)

        with pytest.raises(ValidationError, match="month rollover"):
            _baseline(
                store,
                business_date=date(2026, 8, 1),
                month_key="2026-08",
                source="prior_session_close",
                source_business_date=date(2026, 7, 31),
                captured_at=datetime(2026, 8, 1, 9, 0, tzinfo=KST),
                confirmed_at=None,
            )

        august_manual = _baseline(
            store,
            business_date=date(2026, 8, 1),
            month_key="2026-08",
            source_business_date=date(2026, 7, 31),
            month_start_equity=10_200_000.0,
            captured_at=datetime(2026, 8, 1, 9, 0, tzinfo=KST),
            confirmed_at=datetime(2026, 8, 1, 9, 1, tzinfo=KST),
        )
        assert (
            store.insert_paper_portfolio_loss_baseline(august_manual)
            == august_manual
        )


def test_prior_session_source_requires_durable_source_day(tmp_path) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        orphan = _baseline(
            store,
            business_date=date(2026, 7, 11),
            source="prior_session_close",
            source_business_date=BUSINESS_DATE,
            captured_at=datetime(2026, 7, 11, 9, 0, tzinfo=KST),
            confirmed_at=None,
        )
        with pytest.raises(PaperStateConflictError, match="durable source day"):
            store.insert_paper_portfolio_loss_baseline(orphan)


def test_manual_baseline_requires_kst_date_and_confirmation(tmp_path) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        with pytest.raises(ValidationError, match="requires confirmation"):
            _baseline(store, confirmed_at=None)
        with pytest.raises(ValidationError, match="month key"):
            _baseline(store, month_key="2026-08")
        with pytest.raises(ValidationError, match="KST business date"):
            _baseline(
                store,
                captured_at=datetime(2026, 7, 9, 9, 0, tzinfo=KST),
            )
        with pytest.raises(ValidationError, match="strictly earlier"):
            _baseline(store, source_business_date=BUSINESS_DATE)


def test_schema_v6_store_migrates_atomically_to_current_version(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    with _paper_store(path) as store:
        prior = store.provenance

    connection = sqlite3.connect(path)
    try:
        payload = json.loads(
            connection.execute(
                "SELECT state_json FROM state_store_metadata"
            ).fetchone()[0]
        )
        payload["schema_version"] = 6
        connection.execute(
            """
            UPDATE state_store_metadata
            SET schema_version = 6, state_json = ?
            WHERE singleton_id = 1
            """,
            (json.dumps(payload, separators=(",", ":"), sort_keys=True),),
        )
        connection.execute("DROP TABLE paper_portfolio_loss_baselines")
        connection.execute("PRAGMA user_version = 6")
        connection.commit()
    finally:
        connection.close()

    with _paper_store(path) as migrated:
        assert migrated.provenance.store_id == prior.store_id
        assert migrated.provenance.schema_version == PAPER_STATE_SCHEMA_VERSION
        assert migrated.load_paper_portfolio_loss_baseline(BUSINESS_DATE) is None

    connection = sqlite3.connect(path)
    try:
        assert (
            connection.execute("PRAGMA user_version").fetchone()[0]
            == PAPER_STATE_SCHEMA_VERSION
        )
        assert connection.execute(
            "SELECT schema_version FROM state_store_metadata"
        ).fetchone()[0] == PAPER_STATE_SCHEMA_VERSION
    finally:
        connection.close()


def test_schema_v7_store_migrates_atomically_to_current_version(tmp_path) -> None:
    path = tmp_path / "paper-v7.sqlite3"
    with _paper_store(path) as store:
        prior_store_id = store.provenance.store_id

    connection = sqlite3.connect(path)
    try:
        payload = json.loads(
            connection.execute(
                "SELECT state_json FROM state_store_metadata"
            ).fetchone()[0]
        )
        payload["schema_version"] = 7
        connection.execute(
            """
            UPDATE state_store_metadata
            SET schema_version = 7, state_json = ?
            WHERE singleton_id = 1
            """,
            (json.dumps(payload, separators=(",", ":"), sort_keys=True),),
        )
        connection.execute("PRAGMA user_version = 7")
        connection.commit()
    finally:
        connection.close()

    with _paper_store(path) as migrated:
        assert migrated.provenance.store_id == prior_store_id
        assert migrated.provenance.schema_version == PAPER_STATE_SCHEMA_VERSION

    connection = sqlite3.connect(path)
    try:
        assert (
            connection.execute("PRAGMA user_version").fetchone()[0]
            == PAPER_STATE_SCHEMA_VERSION
        )
        assert connection.execute(
            "SELECT schema_version FROM state_store_metadata"
        ).fetchone()[0] == PAPER_STATE_SCHEMA_VERSION
    finally:
        connection.close()


def test_loss_baseline_schema_contains_no_raw_secrets_or_account_id(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    with _paper_store(path) as store:
        baseline = _baseline(store)
        store.insert_paper_portfolio_loss_baseline(baseline)
        with pytest.raises(ValidationError):
            PaperPortfolioLossBaseline.model_validate(
                {**baseline.model_dump(), "api_key": "must-not-persist"}
            )

    connection = sqlite3.connect(path)
    try:
        payload = json.loads(
            connection.execute(
                "SELECT state_json FROM paper_portfolio_loss_baselines"
            ).fetchone()[0]
        )
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(paper_portfolio_loss_baselines)"
            )
        }
    finally:
        connection.close()

    forbidden = {
        "account_id",
        "account_number",
        "api_key",
        "api_secret",
        "access_token",
        "authorization",
        "credential",
    }
    assert not forbidden.intersection(payload)
    assert not forbidden.intersection(columns)
    assert payload["account_scope_fingerprint"] == ACCOUNT_A
