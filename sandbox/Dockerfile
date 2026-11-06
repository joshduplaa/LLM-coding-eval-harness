# Sandbox image: the only place model-generated code ever runs.
# Hardening (no network, read-only rootfs, resource limits, dropped capabilities) is applied
# at `docker run` time by src/evalharness/sandbox.py. This image only provides:
#   - a pinned Python + pytest
#   - the trusted wrapper and plugin, owned by root and read-only
#   - an unprivileged user to run as
FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54

RUN pip install --no-cache-dir pytest==9.2.0 \
 && useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin sandbox

COPY entrypoint.py harness_plugin.py /opt/harness/
RUN chmod 0555 /opt/harness && chmod 0444 /opt/harness/*.py

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/harness

USER 10001:10001
WORKDIR /tmp
ENTRYPOINT ["python", "-I", "/opt/harness/entrypoint.py"]
