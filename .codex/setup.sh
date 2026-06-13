#!/usr/bin/env sh
set -eu

python -m pip install -e ".[test]"
python -m pytest quantpilot/tests
