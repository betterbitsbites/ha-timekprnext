#!/bin/sh
# Options are read by tk_budget.main from /data/options.json.
# s6-overlay's /init does not pass the image ENV through, so set paths here.
export PYTHONPATH=/app PYTHONUNBUFFERED=1
cd /app
exec /opt/venv/bin/python3 -m tk_budget.main
