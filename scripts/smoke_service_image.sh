#!/usr/bin/env bash
# Start the service image and check it from the outside. Used by CI and `make smoke`.
# Usage: scripts/smoke_service_image.sh [image]
set -euo pipefail
IMAGE="${1:-evalharness-service:0.5.0}"
NAME="evalharness-smoke-$$"
PORT="${SMOKE_PORT:-18000}"

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

fail() { echo "SMOKE FAIL: $*" >&2; docker logs "$NAME" 2>&1 | tail -20 >&2 || true; exit 1; }

docker run -d --name "$NAME" -p "127.0.0.1:${PORT}:8000" "$IMAGE" >/dev/null

# Wait for Docker's own HEALTHCHECK to report healthy (max ~30 s).
for _ in $(seq 1 30); do
  status="$(docker inspect -f '{{.State.Health.Status}}' "$NAME")"
  [ "$status" = "healthy" ] && break
  [ "$(docker inspect -f '{{.State.Running}}' "$NAME")" = "true" ] || fail "container exited"
  sleep 1
done
[ "$status" = "healthy" ] || fail "never became healthy (last status: $status)"

base="http://127.0.0.1:${PORT}"
curl -fsS "$base/healthz" | grep -q '"status":"ok"' || fail "/healthz"
# If the image tag looks like a version, the running code must report that same version.
tag="${IMAGE##*:}"
if [[ "$tag" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  curl -fsS "$base/healthz" | grep -q "\"version\":\"$tag\"" || fail "/healthz version is not $tag"
fi
curl -fsS "$base/tasks" | grep -q '"rle_encode"' || fail "/tasks does not list the bundled tasks"
curl -fsS "$base/runs" | grep -qx '\[\]' || fail "/runs should be empty in a fresh image"
code="$(curl -s -o /dev/null -w '%{http_code}' "$base/runs/nope")"
[ "$code" = "404" ] || fail "/runs/nope returned $code, expected 404"
code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$base/runs")"
[ "$code" = "405" ] || fail "POST /runs returned $code, expected 405 (API must be read-only)"

# Image hygiene: runs as non-root, and nothing from the developer's machine leaked in.
[ "$(docker exec "$NAME" id -u)" = "10002" ] || fail "service is not running as uid 10002"
docker exec "$NAME" sh -c 'test ! -e /app/.env && test ! -e /app/.git && test ! -e /app/results && test ! -e /app/runs && test ! -e /bin/uv' \
  || fail "image contains files it should not (.env, .git, results, runs, or uv)"

echo "SMOKE OK: $IMAGE (healthy, endpoints correct, non-root, clean)"
