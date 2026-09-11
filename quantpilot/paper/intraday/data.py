"""Durable, point-in-time intraday research observations.

The ledger is append-only: exact observations are idempotent, while revisions
to an already observed bar, universe snapshot, event ID, or source definition
are rejected.  Timestamps are persisted as canonical UTC ISO-8601 strings.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Iterator, Mapping, Sequence

from quantpilot.paper.strategy import Bar, validate_bars


SCHEMA_VERSION = 1
DATA_MODES = frozenset(
    {
        "fixture",
        "local_historical",
        "external_historical",
        "realtime_market_data",
    }
)
ALLOWED_PROVIDERS = frozenset({"kis_paper", "free_external", "fixture"})
_SOURCE_FIELDS = frozenset(
    {"provider", "mode", "license", "license_verified", "availability_basis"}
)
_DATASET_FIELDS = frozenset(
    {
        "schema_version",
        "data_mode",
        "bars",
        "universes",
        "events",
        "provenance",
        "sha256",
    }
)
_BAR_FIELDS = frozenset(
    {
        "symbol",
        "start",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "available_at",
        "source",
    }
)
_UNIVERSE_FIELDS = frozenset({"at", "source", "symbols", "metadata"})
_EVENT_COMMON_FIELDS = frozenset(
    {"id", "kind", "symbol", "event_at", "received_at", "source"}
)
_EVENT_KIND_FIELDS = {
    "tick": frozenset({"price", "quantity"}),
    "quote": frozenset({"bid", "ask", "bid_size", "ask_size"}),
}


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty trimmed string")
    return value


def _utc(value: datetime | str, name: str) -> tuple[datetime, str]:
    if isinstance(value, str):
        if not value or value != value.strip():
            raise ValueError(f"{name} must be a timezone-aware ISO timestamp")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"{name} must be a timezone-aware ISO timestamp") from None
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise TypeError(f"{name} must be a datetime or ISO timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    normalized = parsed.astimezone(timezone.utc)
    return normalized, normalized.isoformat()


def _number(value: object, name: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result) or (result < 0.0 if allow_zero else result <= 0.0):
        qualifier = "nonnegative" if allow_zero else "positive"
        raise ValueError(f"{name} must be finite and {qualifier}")
    return result


def _json_object(value: object, name: str) -> dict:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be an object")
    try:
        encoded = _canonical_json(value)
        decoded = json.loads(encoded)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be JSON-safe") from None
    if not isinstance(decoded, dict):
        raise TypeError(f"{name} must be an object")
    return decoded


class IntradayData:
    """SQLite-backed intraday research ledger.

    ``data_mode`` is required for a new non-fixture ledger.  Passing ``None``
    reopens an existing ledger's mode, or creates a fixture ledger for the
    convenient ``IntradayData(path)`` interface.
    """

    def __init__(self, path: str | Path, data_mode: str | None = None) -> None:
        self.path = Path(path)
        if data_mode is not None and data_mode not in DATA_MODES:
            raise ValueError(f"unsupported data_mode: {data_mode!r}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, isolation_level=None)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.row_factory = sqlite3.Row
        self._transaction_depth = 0
        try:
            self._create_schema()
            stored = self._connection.execute(
                "SELECT value FROM ledger_metadata WHERE key = 'data_mode'"
            ).fetchone()
            if stored is None:
                selected = data_mode or "fixture"
                self._connection.execute(
                    "INSERT INTO ledger_metadata(key, value) VALUES ('data_mode', ?)",
                    (selected,),
                )
            else:
                selected = str(stored["value"])
                if selected not in DATA_MODES:
                    raise ValueError(
                        f"ledger contains unsupported data_mode: {selected!r}"
                    )
                if data_mode is not None and data_mode != selected:
                    raise ValueError(
                        f"ledger data_mode is {selected!r}, not {data_mode!r}"
                    )
            self.data_mode = selected
        except Exception:
            self._connection.close()
            raise

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS ledger_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sources (
                source TEXT PRIMARY KEY,
                metadata_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bars (
                symbol TEXT NOT NULL,
                start TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                available_at TEXT NOT NULL,
                source TEXT NOT NULL REFERENCES sources(source),
                PRIMARY KEY(symbol, start, source)
            );
            CREATE TABLE IF NOT EXISTS bar_identities (
                symbol TEXT NOT NULL,
                start TEXT NOT NULL,
                content_json TEXT NOT NULL,
                PRIMARY KEY(symbol, start)
            );
            CREATE TABLE IF NOT EXISTS universes (
                at TEXT NOT NULL,
                source TEXT NOT NULL REFERENCES sources(source),
                symbols_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                PRIMARY KEY(at, source)
            );
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                event_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_received_idx ON events(json_extract(event_json,'$.received_at'));
            """
        )

    def close(self) -> None:
        """Close the underlying SQLite connection."""

        self._connection.close()

    def __enter__(self) -> "IntradayData":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator["IntradayData"]:
        """Run ledger writes atomically, rolling back the group on an error."""

        outermost = self._transaction_depth == 0
        if outermost:
            self._connection.execute("BEGIN IMMEDIATE")
        self._transaction_depth += 1
        try:
            yield self
        except Exception:
            self._transaction_depth -= 1
            if outermost:
                self._connection.execute("ROLLBACK")
            raise
        else:
            self._transaction_depth -= 1
            if outermost:
                self._connection.execute("COMMIT")

    def _write(self, operation) -> bool:
        if self._transaction_depth:
            return bool(operation())
        with self.transaction():
            return bool(operation())

    def register_source(self, source: str, metadata: Mapping[str, object]) -> bool:
        """Register immutable source provenance, returning False if already present."""

        source = _text(source, "source")
        normalized = _json_object(metadata, "metadata")
        if frozenset(normalized) != _SOURCE_FIELDS:
            raise ValueError("source metadata fields are invalid")
        provider = normalized.get("provider")
        mode = normalized.get("mode")
        license_name = normalized.get("license")
        verified = normalized.get("license_verified")
        basis = normalized.get("availability_basis")
        if provider not in ALLOWED_PROVIDERS:
            raise ValueError("source provider is not allowed")
        if provider == "fixture" and mode != "fixture":
            raise ValueError("fixture_source_cannot_claim_market_evidence")
        if mode not in DATA_MODES or mode != self.data_mode:
            raise ValueError("source mode does not match the ledger data_mode")
        _text(license_name, "license")
        _text(basis, "availability_basis")
        if not isinstance(verified, bool):
            raise TypeError("license_verified must be a bool")
        policy_text = " ".join((source, str(license_name), str(basis))).lower()
        if (
            "paid" in policy_text
            or "auto-fallback" in policy_text
            or "auto_fallback" in policy_text
        ):
            raise ValueError("paid or automatic fallback sources are forbidden")
        if provider == "free_external" and not verified:
            raise ValueError("external source license must be verified")
        if (
            mode in {"local_historical", "external_historical"}
            and basis != "historical_exchange_completion"
        ):
            raise ValueError(
                "historical sources must label historical_exchange_completion availability"
            )
        if mode == "realtime_market_data" and basis not in {
            "collected_at",
            "received_at",
        }:
            raise ValueError("realtime sources require collected/received evidence")
        encoded = _canonical_json(normalized)

        def operation() -> bool:
            current = self._connection.execute(
                "SELECT metadata_json FROM sources WHERE source = ?", (source,)
            ).fetchone()
            if current is not None:
                if current["metadata_json"] != encoded:
                    raise ValueError("source provenance cannot be revised")
                return False
            self._connection.execute(
                "INSERT INTO sources(source, metadata_json) VALUES (?, ?)",
                (source, encoded),
            )
            return True

        return self._write(operation)

    def _source(self, source: object) -> tuple[str, dict]:
        name = _text(source, "source")
        row = self._connection.execute(
            "SELECT metadata_json FROM sources WHERE source = ?", (name,)
        ).fetchone()
        if row is None:
            raise ValueError(f"source is not registered: {name}")
        return name, json.loads(row["metadata_json"])

    def record_bar(self, bar: Bar, available_at: datetime | str, source: str) -> bool:
        """Append one completed one-minute bar observation.

        The observation time may not precede the bar's one-minute completion.
        Historical exchange-completion timestamps remain explicitly labelled by
        their registered provenance rather than being presented as collection time.
        """

        if not isinstance(bar, Bar):
            raise TypeError("bar must be a Bar")
        _, start = _utc(bar.start, "bar.start")
        available_dt, available = _utc(available_at, "available_at")
        validate_bars([bar], available_dt)
        source, _ = self._source(source)
        content = {
            "symbol": bar.symbol,
            "start": start,
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": float(bar.volume),
        }
        content_json = _canonical_json(content)

        def operation() -> bool:
            identity = self._connection.execute(
                "SELECT content_json FROM bar_identities WHERE symbol = ? AND start = ?",
                (bar.symbol, start),
            ).fetchone()
            if identity is not None and identity["content_json"] != content_json:
                raise ValueError("observed bar cannot be revised")
            current = self._connection.execute(
                "SELECT * FROM bars WHERE symbol = ? AND start = ? AND source = ?",
                (bar.symbol, start, source),
            ).fetchone()
            if current is not None:
                return False
            if identity is None:
                self._connection.execute(
                    "INSERT INTO bar_identities(symbol, start, content_json) VALUES (?, ?, ?)",
                    (bar.symbol, start, content_json),
                )
            self._connection.execute(
                "INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    bar.symbol,
                    start,
                    content["open"],
                    content["high"],
                    content["low"],
                    content["close"],
                    content["volume"],
                    available,
                    source,
                ),
            )
            return True

        return self._write(operation)

    def record_universe(
        self,
        symbols: Sequence[str],
        at: datetime | str,
        source: str,
        metadata: Mapping[str, object] | None = None,
    ) -> bool:
        """Append an observed point-in-time universe without backfilling symbols."""

        if isinstance(symbols, (str, bytes)) or not isinstance(symbols, Sequence):
            raise TypeError("symbols must be a sequence")
        normalized_symbols: list[str] = []
        seen: set[str] = set()
        for symbol in symbols:
            item = _text(symbol, "symbol")
            if item in seen:
                raise ValueError("universe symbols must be unique")
            seen.add(item)
            normalized_symbols.append(item)
        _, at_text = _utc(at, "at")
        source, _ = self._source(source)
        normalized_metadata = _json_object(metadata or {}, "metadata")
        symbols_json = _canonical_json(normalized_symbols)
        metadata_json = _canonical_json(normalized_metadata)

        def operation() -> bool:
            current = self._connection.execute(
                "SELECT symbols_json, metadata_json FROM universes WHERE at = ? AND source = ?",
                (at_text, source),
            ).fetchone()
            if current is not None:
                if (
                    current["symbols_json"] != symbols_json
                    or current["metadata_json"] != metadata_json
                ):
                    raise ValueError("universe observation cannot be revised")
                return False
            self._connection.execute(
                "INSERT INTO universes VALUES (?, ?, ?, ?)",
                (at_text, source, symbols_json, metadata_json),
            )
            return True

        return self._write(operation)

    def _normalize_event(self, event: Mapping[str, object]) -> dict:
        value = _json_object(event, "event")
        kind = value.get("kind")
        if kind not in _EVENT_KIND_FIELDS:
            raise ValueError("event kind must be tick or quote")
        expected = _EVENT_COMMON_FIELDS | _EVENT_KIND_FIELDS[kind]
        if frozenset(value) != expected:
            raise ValueError("event fields are invalid")
        event_id = _text(value.get("id"), "event.id")
        symbol = _text(value.get("symbol"), "event.symbol")
        source, provenance = self._source(value.get("source"))
        event_dt, event_at = _utc(value.get("event_at"), "event.event_at")
        received_dt, received_at = _utc(value.get("received_at"), "event.received_at")
        if received_dt < event_dt:
            raise ValueError("received_at cannot precede event_at")
        if provenance["mode"] == "realtime_market_data" and provenance[
            "availability_basis"
        ] not in {
            "collected_at",
            "received_at",
        }:
            raise ValueError("realtime event lacks received evidence")
        normalized = {
            "id": event_id,
            "kind": kind,
            "symbol": symbol,
            "event_at": event_at,
            "received_at": received_at,
            "source": source,
        }
        if kind == "tick":
            normalized.update(
                price=_number(value.get("price"), "event.price"),
                quantity=_number(value.get("quantity"), "event.quantity"),
            )
        else:
            bid = _number(value.get("bid"), "event.bid")
            ask = _number(value.get("ask"), "event.ask")
            if bid > ask:
                raise ValueError("quote must be noncrossed")
            normalized.update(
                bid=bid,
                ask=ask,
                bid_size=_number(
                    value.get("bid_size"), "event.bid_size", allow_zero=True
                ),
                ask_size=_number(
                    value.get("ask_size"), "event.ask_size", allow_zero=True
                ),
            )
        return normalized

    def record_event(self, event: Mapping[str, object]) -> bool:
        """Append a validated tick or quote, preserving event and receipt times."""

        normalized = self._normalize_event(event)
        encoded = _canonical_json(normalized)

        def operation() -> bool:
            current = self._connection.execute(
                "SELECT event_json FROM events WHERE id = ?", (normalized["id"],)
            ).fetchone()
            if current is not None:
                previous = json.loads(current["event_json"])
                replayed = dict(normalized, received_at=previous["received_at"])
                if (
                    previous != replayed
                    or normalized["received_at"] < previous["received_at"]
                ):
                    raise ValueError("event ID cannot be revised")
                return False
            self._connection.execute(
                "INSERT INTO events(id, event_json) VALUES (?, ?)",
                (normalized["id"], encoded),
            )
            return True

        return self._write(operation)

    def _payload(self, since=None, include_events=True) -> dict:
        boundary = _utc(since, "since")[1] if since is not None else ""
        bars = [
            dict(row)
            for row in self._connection.execute(
                "SELECT symbol, start, open, high, low, close, volume, available_at, source "
                "FROM bars WHERE start>=? ORDER BY start, symbol, source",
                (boundary,),
            )
        ]
        universes = [
            {
                "at": row["at"],
                "source": row["source"],
                "symbols": json.loads(row["symbols_json"]),
                "metadata": json.loads(row["metadata_json"]),
            }
            for row in self._connection.execute(
                "SELECT * FROM universes WHERE at>=? ORDER BY at, source", (boundary,)
            )
        ]
        events = (
            [
                json.loads(row["event_json"])
                for row in self._connection.execute(
                    "SELECT event_json FROM events WHERE json_extract(event_json,'$.received_at')>=? ORDER BY id",
                    (boundary,),
                )
            ]
            if include_events
            else []
        )
        provenance = {
            row["source"]: json.loads(row["metadata_json"])
            for row in self._connection.execute(
                "SELECT source, metadata_json FROM sources ORDER BY source"
            )
        }
        return {
            "schema_version": SCHEMA_VERSION,
            "data_mode": self.data_mode,
            "bars": bars,
            "universes": universes,
            "events": events,
            "provenance": provenance,
        }

    def dataset(self, *, since=None, include_events=True) -> dict:
        """Return a deterministic JSON-safe dataset plus its canonical SHA-256."""

        payload = self._payload(since, include_events)
        return {**payload, "sha256": _digest(payload)}

    def ingest_bundle(self, payload: Mapping[str, object]) -> dict:
        """Strictly validate and atomically import a complete dataset bundle."""

        bundle = _json_object(payload, "payload")
        if frozenset(bundle) != _DATASET_FIELDS:
            raise ValueError("dataset fields are invalid")
        if bundle["schema_version"] != SCHEMA_VERSION:
            raise ValueError("unsupported dataset schema_version")
        if bundle["data_mode"] != self.data_mode:
            raise ValueError("dataset data_mode does not match ledger")
        supplied_hash = bundle["sha256"]
        if not isinstance(supplied_hash, str) or supplied_hash != _digest(
            {key: bundle[key] for key in bundle if key != "sha256"}
        ):
            raise ValueError("dataset sha256 mismatch")
        if not isinstance(bundle["provenance"], dict):
            raise TypeError("provenance must be an object")
        for name in ("bars", "universes", "events"):
            if not isinstance(bundle[name], list):
                raise TypeError(f"{name} must be a list")

        with self.transaction():
            for source, metadata in bundle["provenance"].items():
                self.register_source(source, metadata)
            for row in bundle["bars"]:
                if not isinstance(row, dict) or frozenset(row) != _BAR_FIELDS:
                    raise ValueError("bar row fields are invalid")
                self.record_bar(
                    Bar(
                        symbol=row["symbol"],
                        start=_utc(row["start"], "bar.start")[0],
                        open=row["open"],
                        high=row["high"],
                        low=row["low"],
                        close=row["close"],
                        volume=row["volume"],
                    ),
                    row["available_at"],
                    row["source"],
                )
            for row in bundle["universes"]:
                if not isinstance(row, dict) or frozenset(row) != _UNIVERSE_FIELDS:
                    raise ValueError("universe row fields are invalid")
                self.record_universe(
                    row["symbols"], row["at"], row["source"], row["metadata"]
                )
            for event in bundle["events"]:
                self.record_event(event)
        return self.dataset()


__all__ = ["DATA_MODES", "IntradayData", "SCHEMA_VERSION"]
