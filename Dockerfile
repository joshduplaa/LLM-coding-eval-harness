# Service image: the read-only results API (evalharness serve).
# It never runs model-generated code. That happens only in sandbox/Dockerfile (see D17).
#
# Build:  docker build -t evalharness-service:0.1.0 .
# Run:    docker run --rm -p 127.0.0.1:8000:8000 -v evalharness-data:/data evalharness-service:0.1.0

# ---- build stage: resolve and install dependencies exactly as locked ----
FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54 AS build
COPY --from=ghcr.io/astral-sh/uv:0.10.2@sha256:94a23af2d50e97b87b522d3cea24aaf8a1faedec1344c952767434f69585cbf9 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first, so this layer is cached until uv.lock changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

# ---- runtime stage: no uv, no build tools, no source tree ----
FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54
RUN useradd --uid 10002 --user-group --no-create-home --shell /usr/sbin/nologin evalharness \
 && mkdir /data && chown evalharness:evalharness /data
COPY --from=build /app/.venv /app/.venv
COPY tasks /app/tasks
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EVALHARNESS_DB=/data/evalharness.sqlite \
    EVALHARNESS_TASKS_DIR=/app/tasks
USER 10002:10002
WORKDIR /data
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
# 0.0.0.0 *inside* the container is required for port publishing to reach it. Exposure to the
# outside world is decided by -p: publish to 127.0.0.1 unless you mean to share it.
CMD ["evalharness", "serve", "--host", "0.0.0.0", "--port", "8000"]
