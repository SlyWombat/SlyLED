# SlyLED orchestrator — container image (#962).
#
#   docker run -d --name slyled --network host --restart unless-stopped \
#       -e TZ=America/Toronto -v slyled-data:/var/lib/slyled \
#       ghcr.io/slywombat/slyled:<ver>
#
# --network host is REQUIRED and makes this image Linux-hosts-only: discovery
# (broadcast PING on UDP 4210), the HinksPix HTTP sweep (#949), Art-Net
# ArtPoll and sACN multicast all need the LAN, and none of them can leave a
# bridge network namespace. Docker Desktop (Windows/macOS) runs the engine in
# a VM, so "host" there is the VM, not your LAN — use SlyLED-Setup.exe on
# Windows. See desktop/linux/README.md.
#
# Same layout and environment as the systemd unit (desktop/linux/slyled.service):
# code in /opt/slyled (no .git → app_dirs treats it as installed), data,
# firmware cache and runtimes under /var/lib/slyled/SlyLED, cache in
# /var/cache/slyled. Runs as the non-root user slyled (uid/gid 1000).
# Version = parent_server.py VERSION; this file never sets or bumps it.

FROM python:3.12-slim-bookworm AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY desktop/linux/requirements.txt /tmp/requirements.txt
# esptool ships as an sdist (pure Python) — built here, nothing to compile.
RUN python -m venv /opt/slyled/.venv \
 && /opt/slyled/.venv/bin/pip install --upgrade pip \
 && /opt/slyled/.venv/bin/pip install -r /tmp/requirements.txt

FROM python:3.12-slim-bookworm
ARG VERSION=dev
ARG REVISION=unknown
ARG CREATED=unknown
LABEL org.opencontainers.image.title="SlyLED Orchestrator" \
      org.opencontainers.image.description="SlyLED lighting orchestrator (headless; run with --network host on a Linux host)" \
      org.opencontainers.image.source="https://github.com/SlyWombat/SlyLED" \
      org.opencontainers.image.vendor="Electric RV Corporation" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.created="${CREATED}"

# libportaudio2: sounddevice (local audio brightness, #879). tzdata: the show
# scheduler's zoneinfo (#954) and TZ for logs.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libportaudio2 tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 1000 slyled \
 && useradd --system --uid 1000 --gid 1000 --home-dir /var/lib/slyled \
            --no-create-home --shell /usr/sbin/nologin slyled

COPY --from=build /opt/slyled/.venv /opt/slyled/.venv
COPY desktop/shared /opt/slyled/desktop/shared
COPY desktop/linux /opt/slyled/desktop/linux
COPY firmware/registry.json /opt/slyled/firmware/registry.json
COPY firmware/orangepi /opt/slyled/firmware/orangepi
COPY docs/help /opt/slyled/docs/help
COPY docs/schema /opt/slyled/docs/schema
COPY docs/USER_MANUAL* /opt/slyled/docs/

RUN sed -n 's/^VERSION *= *"\([0-9.]*\)".*/\1/p' /opt/slyled/desktop/shared/parent_server.py \
        > /opt/slyled/VERSION \
 && /opt/slyled/.venv/bin/python -c "import cv2, numpy, sounddevice, psutil, esptool, paramiko, flask, waitress, yaml, serial, qrcode, PIL; import zoneinfo; zoneinfo.ZoneInfo('America/Toronto')" \
 && /opt/slyled/.venv/bin/python -m compileall -q /opt/slyled/desktop/shared \
 && install -d -o slyled -g slyled -m 0755 /var/lib/slyled /var/cache/slyled

ENV XDG_DATA_HOME=/var/lib/slyled \
    XDG_CACHE_HOME=/var/cache/slyled \
    PYTHONUNBUFFERED=1 \
    SLYLED_PORT=8080

VOLUME /var/lib/slyled
WORKDIR /opt/slyled/desktop/shared
USER slyled

# Metadata only: with --network host nothing is published. Same set the
# Linux installer opens in the firewall (install.sh).
EXPOSE 8080/tcp 4210/udp 4211/udp 5568/udp 6454/udp

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["/opt/slyled/.venv/bin/python", "-c", "import os, sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/status' % os.environ.get('SLYLED_PORT', '8080'), timeout=3).status == 200 else 1)"]

# Exec form: python is PID 1 and gets SIGTERM directly → the server's signal
# handler blacks out / stops the DMX engines, runs the scheduler's HinksPix
# hand-off and exits 0. The port comes from SLYLED_PORT (or append --port N).
STOPSIGNAL SIGTERM
ENTRYPOINT ["/opt/slyled/.venv/bin/python", "-u", "/opt/slyled/desktop/shared/parent_server.py", "--no-browser"]
