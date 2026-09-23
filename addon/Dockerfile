ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8

# Pinned runtime deps. websocket-client powers the HA area/entity registry read
# for floor-plan overlays and is a hard requirement (the /api/ha/areas route
# 502s without it); waitress is the production WSGI server.
RUN apk add --no-cache python3 py3-pip \
    && pip3 install --no-cache-dir --break-system-packages \
        "Flask==3.0.3" \
        "requests==2.32.3" \
        "websocket-client==1.8.0" \
        "waitress==3.0.0"

WORKDIR /app
COPY app/ /app/
COPY rootfs/ /
# so the GUI can show the running add-on version (read by server._addon_version)
COPY config.yaml /config.yaml

# The run script is authored on Windows (CRLF) and copied without the exec bit;
# normalise line endings and make it executable so s6 can start it.
RUN sed -i 's/\r$//' /etc/services.d/dreame_scheduler/run \
    && chmod +x /etc/services.d/dreame_scheduler/run

EXPOSE 8099

# s6-overlay (init: false in config.yaml) starts the service in
# rootfs/etc/services.d/dreame_scheduler/run.
