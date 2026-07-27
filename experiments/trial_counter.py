"""Append-only trial ledger + automatic threshold inflation.

Why this exists
---------------
A backtest that looks good after 30 attempts is not evidence; it is the maximum
of 30 draws. The Quantopian study of 888 live-deployed algorithms found
in-sample Sharpe explained almost none of out-of-sample performance
(R^2 < 0.025) -- selection is that strong.

So the rule agreed for this project is NOT "stop searching". It is:

    search as much as you like, but every look raises the bar.

This module makes that automatic. Experiments call `register(...)` once per
parameter set they EVALUATE. The ledger is append-only; nothing is ever
rewritten, so the count cannot be quietly reset.

deduced vs searched
-------------------
The test is not "did you change a parameter" but "what did you look at to
choose it":

    deduced  - fixed from horizon, purpose, or an outside convention,
               without looking at this data's results.        NOT counted.
    searched - chosen or kept because of what the results showed.  COUNTED.

Threshold inflation
-------------------
With N searched trials, the expected best result under a true null is not zero.
Using the Bailey & Lopez de Prado expected-maximum approximation:

    E[max of N standard normals] ~= (1-g)*Phi^-1(1 - 1/N) + g*Phi^-1(1 - 1/(N*e))

with g = Euler-Mascheroni. Multiply by the standard error of the effect you are
measuring and that is how much a null result would look like by luck alone.
The required effect becomes  base + haircut.

Research only. No broker, no network.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

LEDGER = Path(__file__).resolve().parent / "trials.jsonl"
EULER_GAMMA = 0.5772156649015329

Kind = Literal["deduced", "searched"]


# --------------------------------------------------------------- statistics
def normal_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation, |err| < 1.15e-9)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def expected_max_of_n(n: int) -> float:
    """Expected maximum of n independent standard normals (Bailey & Lopez de Prado)."""
    if n <= 1:
        return 0.0
    return (1 - EULER_GAMMA) * normal_ppf(1 - 1 / n) + EULER_GAMMA * normal_ppf(1 - 1 / (n * math.e))


def required_effect(base: float, standard_error: float, searched: int) -> float:
    """Effect size a result must clear, inflated for the number of looks taken."""
    return base + expected_max_of_n(searched) * standard_error


# ------------------------------------------------------------------ ledger
@dataclass
class Trial:
    ts: str
    experiment: str
    kind: Kind
    params_hash: str
    params: dict[str, Any]
    reason: str
    outcome: str
    duplicate: bool


def _hash(experiment: str, params: dict[str, Any]) -> str:
    payload = json.dumps({"e": experiment, "p": params}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def read_all() -> list[Trial]:
    if not LEDGER.exists():
        return []
    out: list[Trial] = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(Trial(**json.loads(line)))
    return out


def register(
    experiment: str,
    params: dict[str, Any],
    *,
    kind: Kind,
    reason: str,
    outcome: str = "",
) -> Trial:
    """Record one evaluated parameter set. Append-only; re-runs are marked duplicate.

    `reason` must say what the value was chosen from. If the honest answer is
    "because the backtest looked better", kind must be "searched".
    """
    if kind not in ("deduced", "searched"):
        raise ValueError("kind must be 'deduced' or 'searched'")
    if not reason.strip():
        raise ValueError("reason is required -- it is the audit trail for kind")

    digest = _hash(experiment, params)
    duplicate = any(t.params_hash == digest for t in read_all())
    trial = Trial(
        ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        experiment=experiment,
        kind=kind,
        params_hash=digest,
        params=params,
        reason=reason,
        outcome=outcome,
        duplicate=duplicate,
    )
    with LEDGER.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(trial.__dict__, ensure_ascii=False) + "\n")
    return trial


def counts() -> dict[str, int]:
    trials = read_all()
    unique_searched = {t.params_hash for t in trials if t.kind == "searched"}
    unique_deduced = {t.params_hash for t in trials if t.kind == "deduced"}
    return {
        "rows": len(trials),
        "searched": len(unique_searched),      # this is the number that inflates the bar
        "deduced": len(unique_deduced),
        "duplicates": sum(1 for t in trials if t.duplicate),
    }


# ------------------------------------------------------------------ report
def report(standard_error: float | None = None, base: float = 0.0) -> str:
    stats = counts()
    n = stats["searched"]
    lines = [
        "=" * 74,
        "TRIAL LEDGER",
        "=" * 74,
        f"  ledger rows            {stats['rows']:>6d}",
        f"  unique SEARCHED sets   {n:>6d}   <- inflates the bar",
        f"  unique deduced sets    {stats['deduced']:>6d}   (from horizon/purpose/convention)",
        f"  duplicate re-runs      {stats['duplicates']:>6d}   (not counted again)",
        "-" * 74,
        f"  E[max of {n} null draws]  {expected_max_of_n(n):>6.2f} standard errors",
    ]
    if standard_error is not None:
        need = required_effect(base, standard_error, n)
        lines += [
            f"  measured standard error {standard_error * 100:>6.2f}%p",
            f"  base threshold          {base * 100:>6.2f}%p",
            f"  REQUIRED now            {need * 100:>6.2f}%p   (base + {expected_max_of_n(n) * standard_error * 100:.2f}%p haircut)",
        ]
    lines += ["-" * 74, "  how the bar grows:"]
    for k in (1, 5, 10, 20, 30, 50, 100):
        marker = "  <- now" if k == n else ""
        lines.append(f"    N={k:<4d}  {expected_max_of_n(k):>5.2f} SE{marker}")
    lines.append("=" * 74)
    return "\n".join(lines)


def by_experiment() -> str:
    trials = read_all()
    groups: dict[str, list[Trial]] = {}
    for trial in trials:
        groups.setdefault(trial.experiment, []).append(trial)
    lines = [f"{'experiment':<26}{'searched':>10}{'deduced':>10}{'outcome':>26}"]
    lines.append("-" * 74)
    for name in sorted(groups):
        rows = groups[name]
        searched = len({t.params_hash for t in rows if t.kind == "searched"})
        deduced = len({t.params_hash for t in rows if t.kind == "deduced"})
        outcome = next((t.outcome for t in reversed(rows) if t.outcome), "")
        lines.append(f"{name:<26}{searched:>10}{deduced:>10}{outcome:>26}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "status"
    if command == "status":
        print(report())
        print()
        print(by_experiment())
    elif command == "threshold":
        if len(argv) < 4:
            print("usage: trial_counter.py threshold <base_pct> <stderr_pct>")
            return 2
        print(report(standard_error=float(argv[3]) / 100, base=float(argv[2]) / 100))
    else:
        print("commands: status | threshold <base_pct> <stderr_pct>")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
