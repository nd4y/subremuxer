# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY pyproject.toml README.md ./
COPY app ./app
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .


FROM python:3.12-slim

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data

# A fixed uid keeps bind-mount ownership predictable across hosts.
RUN groupadd --gid 10001 subremuxer \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin subremuxer \
    && mkdir -p /data \
    && chown 10001:10001 /data

COPY --from=builder /opt/venv /opt/venv
COPY --chown=10001:10001 app /app/app
COPY docker-entrypoint.py /usr/local/bin/docker-entrypoint.py
RUN chmod +x /usr/local/bin/docker-entrypoint.py

WORKDIR /app
# Deliberately no USER: the entrypoint starts as root only long enough to make a
# root-owned volume writable, then drops to uid 10001 before exec'ing the app.
# Declaring USER here would make that impossible on platforms that mount volumes
# root-owned and give no way to change it.
#
# No VOLUME declaration either: it only creates an anonymous volume, every
# deployment here mounts /data explicitly anyway, and Railway rejects a Dockerfile
# that contains one outright ("docker VOLUME is not supported, use Railway Volumes").
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; port=os.getenv('PORT','8000'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=4).status == 200 else 1)"

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.py"]

# PORT is honoured because platforms like Railway assign one and route to it;
# without this the app would listen on 8000 and never pass their health check.
# --forwarded-allow-ips is safe here: the container is only reachable through the
# reverse proxy in front of it.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} \
     --proxy-headers --forwarded-allow-ips '*'"]
