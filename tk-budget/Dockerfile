# Build context is this directory (the HA Supervisor builds add-ons that way).
# tk_protocol is vendored into vendor/ by scripts/sync-protocol.sh.
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-pip \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir "paho-mqtt>=2.0,<3"

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH="/app" \
    PYTHONUNBUFFERED=1

COPY vendor/tk_protocol /app/tk_protocol
COPY tk_budget /app/tk_budget
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
