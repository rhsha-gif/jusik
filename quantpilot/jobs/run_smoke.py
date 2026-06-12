from __future__ import annotations

import json

from quantpilot.packages.core.harness_service import HarnessService


def main() -> int:
    summary = HarnessService().run_smoke()
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
