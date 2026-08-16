# ======================================================================
# ALL-IN-ONE fq_cube image: Cube Store (embedded) + API + inline refresh,
# all in a single container. Deploy architecture decision: instead of
# the classic split (3 workloads, 2 images), we run a single image.
#
# Since Cube in production (CUBEJS_DEV_MODE=false) does NOT spin up an
# embedded Cube Store on its own, we pull the `cubestored` binary from the
# official Cube Store image (multi-stage) and the custom entrypoint starts
# both processes.
#
# BEST PRACTICE: PINNED version (never :latest). Check the latest stable at
# https://hub.docker.com/r/cubejs/cube/tags and update the ARG below.
# Keep CUBE_VERSION identical for both images (cube and cubestore).
# ======================================================================
ARG CUBE_VERSION=v1.7.19

# --- Helper stage: only to extract the Cube Store binary ---
FROM cubejs/cubestore:${CUBE_VERSION} AS cubestore

# --- Final image: Cube API + Cube Store binary ---
FROM cubejs/cube:${CUBE_VERSION}

# Cube Store binary from the official image (confirmed path: /cube/cubestored).
# Copied to /usr/local/bin (on PATH) so the entrypoint can call `cubestored`.
COPY --from=cubestore /cube/cubestored /usr/local/bin/cubestored

# Cube expects the project (config + model) in /cube/conf
COPY cube.py /cube/conf/cube.py
COPY rbac.py /cube/conf/rbac.py
COPY model/ /cube/conf/model/

# Entrypoint that starts the local Cube Store and then the API
COPY docker-entrypoint-allinone.sh /cube/conf/docker-entrypoint-allinone.sh
# Strip CR before chmod. .gitattributes already pins LF, but that only protects clones — a ZIP
# download or a stray core.autocrlf still yields CRLF, and then the shebang resolves to `bash`
# and the container dies with a message that names neither the file nor the cause.
RUN sed -i 's/$//' /cube/conf/docker-entrypoint-allinone.sh     && chmod +x /cube/conf/docker-entrypoint-allinone.sh

# Data directory for the embedded Cube Store (columnar cache)
ENV CUBESTORE_DATA_DIR=/cube/data
RUN mkdir -p /cube/data

# In production, dev mode must be OFF (set via env in the deploy):
#   CUBEJS_DEV_MODE=false
ENTRYPOINT ["/cube/conf/docker-entrypoint-allinone.sh"]
