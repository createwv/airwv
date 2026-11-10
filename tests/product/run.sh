#!/usr/bin/env bash
# Product harness: boot the app on the local SQLite copy, drive the key pages headless at
# 390/1280, assert the smoke checks, write screenshots + report.md to tests/product/out/.
#   tests/product/run.sh            # extra args go to pytest, e.g. -k home
#   AIRWV_PRODUCT_DB=seed tests/product/run.sh   # fake data instead of the local copy
set -euo pipefail
cd "$(dirname "$0")/../.."
.venv/bin/python -m pytest tests/product -q -rxs "$@"
echo "report: tests/product/out/report.md · screenshots: tests/product/out/*.png"
