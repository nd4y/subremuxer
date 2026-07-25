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

WORKDIR /app
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

# --forwarded-allow-ips is safe here: the container is only reachable through the
# reverse proxy in front of it.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
