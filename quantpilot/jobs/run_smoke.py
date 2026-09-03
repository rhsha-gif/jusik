from __future__ import annotations

import json

from quantpilot.packages.core.harness_service import HarnessService, run_smoke_with_operator
from quantpilot.services.api.dependencies import validate_generic_runtime_environment


def main() -> int:
    validate_generic_runtime_environment()
    harness = HarnessService.from_environment()
    summary = run_smoke_with_operator(harness)

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
