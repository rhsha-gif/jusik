"""One explicit, credential-scoped budget for every paper client entry point."""

from hashlib import sha256
from pathlib import Path


def budget_identity(config):
    # Different accounts using the same app credential consume the same API limit.
    return "sha256:" + sha256(("kis-paper-budget\0" + config.app_key).encode()).hexdigest()


def shared_transport(config, transport, *, enabled=False, directory=None, observer=None):
    from quantpilot.paper.data import LimitedTransport
    identity = budget_identity(config)
    root = Path(directory) if directory is not None else Path.home() / ".quantpilot" / "api-budgets"
    path = root / (identity.split(":")[1] + ".sqlite3")
    # Once a profile opts in, independent probes using that credential join its budget.
    # A fresh installation still defaults to the existing local limiter.
    if not enabled and not path.is_file():
        return LimitedTransport(transport)
    from quantpilot.paper.api_budget import BudgetTransport, SharedBudget
    root.mkdir(parents=True, exist_ok=True)
    budget = SharedBudget(path, identity, observer=observer)
    return BudgetTransport(transport, budget)
