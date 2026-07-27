"""Evidence record schema + deterministic validator.

The LLM's entire job in this system is to emit records in this shape. A record
that fails ANY check below never reaches scoring, no matter how plausible its
prose is. The checks are the point: they convert "the model said so" into
"the model pointed at a real sentence in a real filing that a human can read".

Checks:
    schema      direction/delta are from closed vocabularies, required fields present
    thesis      theme_node exists in the registered thesis file
    provenance  source_accession exists in the local point-in-time index
    quotation   exact_source_span occurs verbatim in the stored filing text
                (whitespace-normalised on both sides -- the store flattens layout)
    numerics    if the span contains digits, the record must either carry
                xbrl_fact_ids or set review_required=true. The LLM never gets to
                assert a number on its own authority.

Usage:
    python evidence.py validate evidence/some_record.json

Research only.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
THESES_DIR = BASE_DIR / "theses"
INDEX = BASE_DIR / "filings" / "_index.csv"

DIRECTIONS = {"confirm", "disconfirm", "neutral"}
DELTAS = {"new", "strengthened", "weakened", "removed"}
REQUIRED = ["theme_id", "theme_node", "direction", "delta", "source_accession", "exact_source_span"]


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Verdict:
    record: str
    passed: bool
    reasons: list[str] = field(default_factory=list)


def load_thesis(theme_id: str) -> dict | None:
    path = THESES_DIR / f"{theme_id.replace('-', '_')}.json"
    if not path.exists():
        candidates = list(THESES_DIR.glob("*.json"))
        for candidate in candidates:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            if data.get("theme_id") == theme_id:
                return data
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def validate(record_path: Path) -> Verdict:
    verdict = Verdict(record=str(record_path), passed=True)

    def fail(reason: str) -> None:
        verdict.passed = False
        verdict.reasons.append(reason)

    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"unreadable record: {exc}")
        return verdict

    for key in REQUIRED:
        if not record.get(key):
            fail(f"missing field: {key}")
    if verdict.reasons:
        return verdict

    if record["direction"] not in DIRECTIONS:
        fail(f"direction '{record['direction']}' not in {sorted(DIRECTIONS)}")
    if record["delta"] not in DELTAS:
        fail(f"delta '{record['delta']}' not in {sorted(DELTAS)}")

    thesis = load_thesis(record["theme_id"])
    if thesis is None:
        fail(f"thesis '{record['theme_id']}' is not registered")
    else:
        nodes = {n["node_id"] for n in thesis.get("nodes", [])}
        if record["theme_node"] not in nodes:
            fail(f"theme_node '{record['theme_node']}' not in thesis nodes {sorted(nodes)}")

    if not INDEX.exists():
        fail("filing index missing -- run fetch_filings.py first")
        return verdict
    with INDEX.open(encoding="utf-8") as handle:
        filings = {row["accession"]: row for row in csv.DictReader(handle)}
    filing = filings.get(record["source_accession"])
    if filing is None:
        fail(f"accession '{record['source_accession']}' not in the point-in-time index")
        return verdict

    text_path = BASE_DIR / filing["text_path"]
    if not text_path.exists():
        fail(f"stored text missing: {text_path.name}")
        return verdict
    document = normalise(text_path.read_text(encoding="utf-8"))
    span = normalise(record["exact_source_span"])
    if len(span) < 20:
        fail("exact_source_span too short (<20 chars) to be verifiable")
    elif span not in document:
        fail("exact_source_span NOT FOUND verbatim in the stored filing text")

    if re.search(r"\d", span):
        has_xbrl = bool(record.get("xbrl_fact_ids"))
        flagged = record.get("review_required") is True
        if not has_xbrl and not flagged:
            fail("span contains numbers but has neither xbrl_fact_ids nor review_required=true")

    return verdict


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] != "validate":
        print(__doc__)
        return 2
    exit_code = 0
    for arg in sys.argv[2:]:
        verdict = validate(Path(arg))
        status = "PASS" if verdict.passed else "FAIL"
        print(f"[{status}] {verdict.record}")
        for reason in verdict.reasons:
            print(f"        - {reason}")
        if not verdict.passed:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
