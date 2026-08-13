# syntax=docker/dockerfile:1
#
# Build:  podman build --format docker -t bulk-mailer .
# Run:    podman run -p 8000:8000 -e SECRET_KEY=... -v mailer-data:/data bulk-mailer
#
# --format docker matters: podman defaults to the OCI image format, which has
# no HEALTHCHECK field and drops the one below with only a warning. Plain
# `docker build` needs no such flag. compose.yaml declares the probe again at
# service level so it works either way.
#
# Every dependency ships a manylinux wheel, so no compiler or apt package is
# needed at build time. The venv is built in a separate stage purely to keep
# pip and its cache out of the final image.

FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt


FROM python:3.13-slim

LABEL org.opencontainers.image.title="Bulk Mailer" \
      org.opencontainers.image.description="Send email to many recipients, with LDAP import" \
      org.opencontainers.image.source="https://github.com/example/python-email" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    # 0.0.0.0 so the port is reachable from outside the container.
    HOST=0.0.0.0 \
    PORT=8000 \
    DATA_DIR=/data \
    # Works out of the box; override with a postgresql+psycopg:// URL.
    DATABASE_URL=sqlite:////data/mailer.db

# Run as an unprivileged user. /data is the only writable path the app needs.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data \
    && chown app:app /data

COPY --from=builder /opt/venv /opt/venv

# Code stays owned by root and world-readable: the app user needs to read it,
# never to modify it. /data below is the only path it can write to.
WORKDIR /app
COPY app/ ./app/
COPY tools/ ./tools/
COPY run.py ./

# A build context from a Windows/exFAT filesystem carries mode 777, which would
# leave the running user able to rewrite its own source. Strip group/other write.
# Only /app: the venv is pip-installed with a sane umask, and chmod-ing it would
# rewrite every file into a second ~126 MB layer.
RUN chmod -R go-w /app

USER app

# Attachments, and the SQLite file when that backend is used.
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "tools/healthcheck.py"]

CMD ["python", "run.py"]
