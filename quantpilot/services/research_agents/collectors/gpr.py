"""Caldara–Iacoviello Geopolitical Risk index (CC BY 4.0).

The authors publish only an `.xls` (measured 2026-09-14: the `.csv` URL is a
404). Reading BIFF needs `xlrd`, which is an optional extra here: when it is
not importable the collector looks for a hand-dropped `local_data/gpr.csv`
(same column names as the sheet) and otherwise returns None with a note, so
the weekly job still runs. No key is involved.
"""

from __future__ import annotations

import csv
import io
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from quantpilot.services.research_agents.collectors.ecos import MacroCollectionError

_USER_AGENT = "QuantPilot-research/1.0"
GPR_ALLOWED_PREFIX = "https://www.matteoiacoviello.com/"
Fetch = Callable[[str], bytes]


def _default_fetch(url: str, timeout_s: float = 60.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - fixed https host  # nosemgrep
        return response.read()


def _month_label(value: Any) -> str | None:
    """'2026-08-01' / '2026-08' / '2026M08' / datetime-ish → 'YYYY-MM'; None when unparseable."""

    if value is None:
        return None
    if hasattr(value, "year") and hasattr(value, "month"):
        return f"{value.year:04d}-{value.month:02d}"
    text = str(value).strip().replace("M", "-").replace("/", "-")
    if len(text) >= 7 and text[4] == "-" and text[:4].isdigit() and text[5:7].isdigit():
        return text[:7]
    if len(text) == 6 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}"
    return None


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _rows_from_table(header: list[str], body: list[list[Any]], columns: dict[str, str]) -> list[dict[str, Any]]:
    index = {name: position for position, name in enumerate(header)}
    missing = [col for col in (columns["month"], columns["gpr"]) if col not in index]
    if missing:
        raise MacroCollectionError(f"gpr: expected columns missing: {missing}")
    out: list[dict[str, Any]] = []
    for row in body:
        month = _month_label(row[index[columns["month"]]] if index[columns["month"]] < len(row) else None)
        if month is None:
            continue
        item: dict[str, Any] = {"month": month}
        for key, col in columns.items():
            if key == "month" or col not in index or index[col] >= len(row):
                continue
            item[key] = _float(row[index[col]])
        if item.get("gpr") is None:
            continue
        out.append(item)
    out.sort(key=lambda r: r["month"])
    return out


def parse_gpr_csv(text: str, columns: dict[str, str]) -> list[dict[str, Any]]:
    reader = list(csv.reader(io.StringIO(text)))
    if not reader:
        return []
    return _rows_from_table([h.strip() for h in reader[0]], [list(r) for r in reader[1:]], columns)


def parse_gpr_xls(raw: bytes, columns: dict[str, str]) -> list[dict[str, Any]]:
    """Requires the optional `xlrd` extra; raises MacroCollectionError when absent."""

    try:
        import xlrd  # type: ignore[import-not-found]
    except ImportError:
        raise MacroCollectionError("gpr: xlrd not installed (optional extra); drop a CSV at local_data/gpr.csv instead") from None
    book = xlrd.open_workbook(file_contents=raw)
    sheet = book.sheet_by_index(0)
    header = [str(sheet.cell_value(0, c)).strip() for c in range(sheet.ncols)]
    body: list[list[Any]] = []
    for r in range(1, sheet.nrows):
        row: list[Any] = []
        for c in range(sheet.ncols):
            cell = sheet.cell(r, c)
            if cell.ctype == xlrd.XL_CELL_DATE:
                row.append(xlrd.xldate_as_datetime(cell.value, book.datemode))
            else:
                row.append(cell.value)
        body.append(row)
    return _rows_from_table(header, body, columns)


def load_gpr(
    *,
    url: str,
    csv_fallback: str | Path,
    columns: dict[str, str],
    repo_root: Path,
    fetch: Fetch | None = None,
) -> tuple[list[dict[str, Any]] | None, str]:
    """(rows, note). A hand-dropped CSV wins over the download; None means skipped, never an exception."""

    csv_path = Path(csv_fallback) if Path(csv_fallback).is_absolute() else repo_root / csv_fallback
    if csv_path.exists():
        try:
            return parse_gpr_csv(csv_path.read_text(encoding="utf-8-sig"), columns), f"gpr: local csv {csv_path.name}"
        except MacroCollectionError as exc:
            return None, str(exc)
    if not url.startswith(GPR_ALLOWED_PREFIX):
        return None, f"gpr: refusing download from an unexpected host (expected {GPR_ALLOWED_PREFIX})"
    try:
        raw = (fetch or _default_fetch)(url)
    except urllib.error.HTTPError as exc:
        return None, f"gpr: download HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, f"gpr: download failed ({type(exc).__name__})"
    try:
        return parse_gpr_xls(raw, columns), "gpr: downloaded xls"
    except MacroCollectionError as exc:
        return None, str(exc)
    except Exception as exc:  # xlrd raises its own hierarchy
        return None, f"gpr: xls parse failed ({type(exc).__name__})"
