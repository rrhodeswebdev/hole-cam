#!/bin/sh
# Every fast check: lint, the Python tests, and the web type-check and unit tests. CI runs this, and so does the
# pre-commit hook (turn it on once per clone: git config core.hooksPath .githooks).
# The browser check is separate and slower: cd web && npm run e2e
set -e
cd "$(dirname "$0")/.."
uv run ruff check
uv run pytest -q
cd web && npm run check
