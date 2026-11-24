#!/usr/bin/with-contenv bashio
# with-contenv passes the container environment (SUPERVISOR_TOKEN) through s6-overlay.
# Options are read by tk_budget.main from /data/options.json.
export PYTHONPATH=/app PYTHONUNBUFFERED=1
cd /app || exit 1
exec /opt/venv/bin/python3 -m tk_budget.main
