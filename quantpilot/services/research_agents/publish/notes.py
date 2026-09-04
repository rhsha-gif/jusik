"""Ledger notes: `<root>/market/YYYY-MM-DD.md` and `<root>/candidates/YYYY-MM-DD-<symbol>.md`.

The root is the private investment ledger (`~/investment-decisions`), never
the public repository. A note for a day that already exists is an error
unless `force` is given: the weekly pipeline's "quiet re-post" failure came
from silently reusing yesterday's product.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

LEDGER_ROOT_ENV = "QUANTPILOT_LEDGER_ROOT"


class NoteExistsError(FileExistsError):
    pass


def ledger_root(root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root)
    configured = os.environ.get(LEDGER_ROOT_ENV)
    return Path(configured) if configured else Path.home() / "investment-decisions"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _frontmatter(fields: dict[str, str]) -> str:
    lines = ["---"] + [f"{key}: {value}" for key, value in fields.items()] + ["---", ""]
    return "\n".join(lines)


def _write(path: Path, frontmatter: dict[str, str], body: str, force: bool) -> Path:
    if path.exists() and not force:
        raise NoteExistsError(f"note already exists for this day: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _frontmatter(frontmatter) + body.strip() + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def write_market_note(
    session_date: str,
    markdown: str,
    *,
    evidence_path: Path,
    generated_by: str,
    root: str | Path | None = None,
    force: bool = False,
) -> Path:
    path = ledger_root(root) / "market" / f"{session_date}.md"
    fields = {
        "type": "market-brief",
        "date": session_date,
        "generated_at": _now_iso(),
        "generated_by": generated_by,
        "evidence": str(Path(evidence_path).resolve()),
        "signal_input": "false",
    }
    return _write(path, fields, markdown, force)


def write_candidate_note(
    session_date: str,
    symbol: str,
    markdown: str,
    *,
    evidence_path: Path,
    generated_by: str,
    root: str | Path | None = None,
    force: bool = False,
) -> Path:
    candidate_id = f"{session_date}-{symbol}"
    path = ledger_root(root) / "candidates" / f"{candidate_id}.md"
    fields = {
        "type": "candidate",
        "candidate_id": candidate_id,
        "symbol": symbol,
        "date": session_date,
        "status": "proposed",
        "generated_at": _now_iso(),
        "generated_by": generated_by,
        "evidence": str(Path(evidence_path).resolve()),
        "signal_input": "false",
    }
    return _write(path, fields, markdown, force)


def read_recent_market_notes(days: int, *, root: str | Path | None = None) -> list[tuple[str, str]]:
    """Newest-first (date, body) pairs for the portfolio-direction and scout prompts."""

    folder = ledger_root(root) / "market"
    if not folder.exists():
        return []
    files = sorted(folder.glob("????-??-??.md"), reverse=True)[:days]
    return [(path.stem, path.read_text(encoding="utf-8")) for path in files]


def read_open_decisions(*, root: str | Path | None = None) -> list[tuple[str, str]]:
    """(decision_id, frontmatter + '## 무효화 조건' section) for every `status: open` record.

    Only those two parts are handed to the direction agent; the rest of a
    decision record (amounts, holdings) stays in the ledger.
    """

    folder = ledger_root(root) / "decisions"
    if not folder.exists():
        return []
    out: list[tuple[str, str]] = []
    for path in sorted(folder.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        parts = text.split("---", 2)
        if len(parts) < 3:
            continue
        frontmatter = parts[1]
        if "status: open" not in frontmatter:
            continue
        body = parts[2]
        section = ""
        marker = "## 무효화 조건"
        if marker in body:
            rest = body.split(marker, 1)[1]
            section = marker + rest.split("\n## ", 1)[0]
        out.append((path.stem, "---" + frontmatter + "---\n" + section.strip()))
    return out
