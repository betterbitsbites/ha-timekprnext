#!/usr/bin/env bash
# Copy protocol/tk_protocol into tk-budget/vendor/ (the HA add-on's Docker build context).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
rm -rf "$root/tk-budget/vendor/tk_protocol"
mkdir -p "$root/tk-budget/vendor/tk_protocol"
cp "$root"/protocol/tk_protocol/*.py "$root/tk-budget/vendor/tk_protocol/"
echo "synced tk_protocol -> tk-budget/vendor/tk_protocol"
