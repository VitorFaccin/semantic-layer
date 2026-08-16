#!/usr/bin/env bash
# ======================================================================
# ALL-IN-ONE entrypoint: starts the embedded Cube Store and then the Cube API
# in the SAME container. Used by the Dockerfile image (single-container deploy).
#
# Flow:
#   1) starts `cubestored` in the background (single-node: router + worker)
#   2) waits for port 3030 (where the API connects) to respond
#   3) points the API at 127.0.0.1:3030 and runs the Cube launcher
#   4) shuts down both processes together on SIGTERM/SIGINT
# ======================================================================
set -euo pipefail

# --- Embedded Cube Store config ---
export CUBESTORE_SERVER_NAME="${CUBESTORE_SERVER_NAME:-127.0.0.1:9999}"
export CUBESTORE_DATA_DIR="${CUBESTORE_DATA_DIR:-/cube/data}"
CUBESTORE_BIND_PORT="${CUBEJS_CUBESTORE_PORT:-3030}"

mkdir -p "${CUBESTORE_DATA_DIR}"

echo "[allinone] starting Cube Store (data dir: ${CUBESTORE_DATA_DIR}, port: ${CUBESTORE_BIND_PORT})..."
cubestored &
CUBESTORE_PID=$!

# Shut down the Cube Store when the container receives a stop signal
term_handler() {
  echo "[allinone] signal received, shutting down Cube Store (pid ${CUBESTORE_PID})..."
  kill -TERM "${CUBESTORE_PID}" 2>/dev/null || true
  wait "${CUBESTORE_PID}" 2>/dev/null || true
  exit 0
}
trap term_handler SIGTERM SIGINT

# --- Wait for the Cube Store to accept connections on port 3030 ---
echo "[allinone] waiting for Cube Store at 127.0.0.1:${CUBESTORE_BIND_PORT}..."
for i in $(seq 1 60); do
  # If the process died, abort early with its exit code
  if ! kill -0 "${CUBESTORE_PID}" 2>/dev/null; then
    echo "[allinone] ERROR: Cube Store exited before becoming ready." >&2
    wait "${CUBESTORE_PID}" || exit $?
    exit 1
  fi
  if (echo > "/dev/tcp/127.0.0.1/${CUBESTORE_BIND_PORT}") >/dev/null 2>&1; then
    echo "[allinone] Cube Store ready."
    break
  fi
  sleep 1
  if [ "${i}" -eq 60 ]; then
    echo "[allinone] ERROR: timeout waiting for Cube Store at ${CUBESTORE_BIND_PORT}." >&2
    exit 1
  fi
done

# --- API connects to the local Cube Store ---
export CUBEJS_CUBESTORE_HOST=127.0.0.1
export CUBEJS_CUBESTORE_PORT="${CUBESTORE_BIND_PORT}"

# --- Run the Cube API launcher ---
# Default entrypoint/CMD of the cubejs/cube image: `docker-entrypoint.sh cubejs server`
# (confirmed via docker inspect). We reuse the original entrypoint to keep the setup.
echo "[allinone] starting the Cube API..."
docker-entrypoint.sh cubejs server &
CUBE_PID=$!

# Wait for either process to finish; if the API goes down, take down the store.
wait -n "${CUBESTORE_PID}" "${CUBE_PID}"
EXIT_CODE=$?
echo "[allinone] a process exited (code ${EXIT_CODE}); shutting down the container."
kill -TERM "${CUBESTORE_PID}" "${CUBE_PID}" 2>/dev/null || true
exit "${EXIT_CODE}"
