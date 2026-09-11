from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace

import pytest

from quantpilot.jobs.collect_intraday_data import collect_once, main
from quantpilot.paper.intraday.data import IntradayData
from quantpilot.paper.strategy import Bar


NOW = datetime(2026, 9, 11, 1, 2, tzinfo=timezone.utc)


def provenance(mode="fixture", provider="fixture", basis="fixture_observed"):
    return {
        "provider": provider,
        "mode": mode,
        "license": "test fixture",
        "license_verified": provider != "free_external",
        "availability_basis": basis,
    }


def make_bar(start=NOW - timedelta(minutes=2), close=100.0):
    return Bar("005930", start, 100.0, 101.0, 99.0, close, 10.0)


def test_duplicate_timezone_equivalence_and_revision_rejection(tmp_path):
    ledger = IntradayData(tmp_path / "ledger.sqlite3")
    ledger.register_source("fixture_minutes", provenance())
    bar = make_bar()
    assert ledger.record_bar(bar, NOW, "fixture_minutes") is True
    equivalent = make_bar(bar.start.astimezone(timezone(timedelta(hours=9))))
    assert ledger.record_bar(equivalent, NOW, "fixture_minutes") is False
    with pytest.raises(ValueError, match="revised"):
        ledger.record_bar(make_bar(close=100.5), NOW, "fixture_minutes")
    dataset = ledger.dataset()
    assert len(dataset["bars"]) == 1
    assert dataset["bars"][0]["start"].endswith("+00:00")
    assert (
        dataset["sha256"]
        == hashlib.sha256(
            json.dumps(
                {key: value for key, value in dataset.items() if key != "sha256"},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
    )
    ledger.close()


def test_completed_and_future_bar_bounds(tmp_path):
    ledger = IntradayData(tmp_path / "ledger.sqlite3")
    ledger.register_source("fixture_minutes", provenance())
    complete = make_bar(NOW - timedelta(minutes=1))
    assert ledger.record_bar(complete, NOW, "fixture_minutes")
    with pytest.raises(ValueError, match="incomplete"):
        ledger.record_bar(make_bar(NOW), NOW, "fixture_minutes")
    ledger.close()


def test_provenance_and_external_license_policy(tmp_path):
    ledger = IntradayData(tmp_path / "ledger.sqlite3", "external_historical")
    bad = provenance(
        "external_historical", "free_external", "historical_exchange_completion"
    )
    bad["license_verified"] = False
    with pytest.raises(ValueError, match="license"):
        ledger.register_source("free_feed", bad)
    bad["license_verified"] = True
    assert ledger.register_source("free_feed", bad)
    assert (
        ledger.dataset()["provenance"]["free_feed"]["availability_basis"]
        == "historical_exchange_completion"
    )
    ledger.close()


def test_event_validation_preserves_event_and_received_times(tmp_path):
    ledger = IntradayData(tmp_path / "ledger.sqlite3")
    ledger.register_source("fixture_events", provenance())
    event = {
        "id": "tick-1",
        "kind": "tick",
        "symbol": "005930",
        "event_at": (NOW - timedelta(seconds=1)).isoformat(),
        "received_at": NOW.isoformat(),
        "source": "fixture_events",
        "price": 100,
        "quantity": 2,
    }
    assert ledger.record_event(event)
    assert ledger.record_event(event) is False
    malformed = {**event, "id": "quote-1", "kind": "quote", "bid": 101, "ask": 100}
    malformed.pop("price")
    malformed.pop("quantity")
    malformed.update(bid_size=1, ask_size=1)
    with pytest.raises(ValueError, match="noncrossed"):
        ledger.record_event(malformed)
    stored = ledger.dataset()["events"][0]
    assert stored["event_at"] != stored["received_at"]
    empty_depth = {**malformed, "bid": 99, "ask": 100, "bid_size": 0, "ask_size": 0}
    assert ledger.record_event(empty_depth)
    assert (
        next(e for e in ledger.dataset()["events"] if e["kind"] == "quote")["ask_size"]
        == 0
    )
    ledger.close()


def test_ingest_bundle_is_atomic(tmp_path):
    source = IntradayData(tmp_path / "source.sqlite3")
    source.register_source("fixture_minutes", provenance())
    source.record_bar(make_bar(), NOW, "fixture_minutes")
    payload = source.dataset()
    payload["events"] = [{"id": "bad"}]
    body = {key: value for key, value in payload.items() if key != "sha256"}
    payload["sha256"] = hashlib.sha256(
        json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    target = IntradayData(tmp_path / "target.sqlite3")
    with pytest.raises(ValueError):
        target.ingest_bundle(payload)
    assert target.dataset()["provenance"] == {}
    assert target.dataset()["bars"] == []
    source.close()
    target.close()


def test_collect_once_records_pit_universe_and_collection_availability(tmp_path):
    ledger = IntradayData(tmp_path / "ledger.sqlite3", "realtime_market_data")
    source_metadata = {
        "provider": "kis_paper",
        "mode": "realtime_market_data",
        "license": "KIS paper API terms",
        "license_verified": True,
        "availability_basis": "collected_at",
    }
    ledger.register_source("kis_paper_ranking", source_metadata)
    ledger.register_source("kis_paper_minutes", source_metadata)
    bar = make_bar()
    market = SimpleNamespace(
        candidates=lambda now, limit: (["005930"], "kis_paper_ranking"),
        minutes=lambda symbol, now: [bar],
    )
    result = collect_once(ledger, market, NOW)
    assert result["status"] == "collected"
    dataset = ledger.dataset()
    assert dataset["universes"] == [
        {
            "at": NOW.isoformat(),
            "source": "kis_paper_ranking",
            "symbols": ["005930"],
            "metadata": {"limit": 20, "selection": "volume_rank"},
        }
    ]
    assert dataset["bars"][0]["available_at"] == NOW.isoformat()
    ledger.close()


def test_collect_once_never_falls_back_and_cli_without_input_is_insufficient(
    tmp_path, capsys
):
    ledger = IntradayData(tmp_path / "ledger.sqlite3", "realtime_market_data")
    result = collect_once(
        ledger,
        SimpleNamespace(candidates=lambda *args: (["005930"], "public_fallback")),
        NOW,
    )
    assert result["status"] == "data_insufficient"
    assert "kis_paper_ranking" in result["issues"][0]["error"]
    assert main(["--ledger", str(tmp_path / "cli.sqlite3"), "--summary"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "data_insufficient"
    ledger.close()


def test_realtime_source_cannot_be_fixture_and_untrusted_errors_are_not_printed(
    tmp_path,
):
    ledger = IntradayData(tmp_path / "ledger.sqlite3", "realtime_market_data")
    with pytest.raises(ValueError, match="fixture_source"):
        ledger.register_source(
            "fixture", provenance("realtime_market_data", "fixture", "collected_at")
        )

    def fail(*args):
        raise RuntimeError("untrusted response body")

    result = collect_once(ledger, SimpleNamespace(candidates=fail), NOW)
    assert "untrusted response body" not in json.dumps(result)
    ledger.close()
