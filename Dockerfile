# NAVIG's MCP server in a container.
#
# Purpose (docs/reference/marketplace-listings.md): an MCP directory can introspect a
# server without a Python environment on the host — it needs an image that starts the
# server and answers introspection. This is also an honest way to run NAVIG's MCP tools on
# a machine where you do not want the kernel installed globally.
#
# What it deliberately does NOT do: mount the host's vault, run the gateway, or hold any
# key. `navig mcp serve` over stdio answers the client that spawned it; the proxy/edge owns
# the network and its authentication.
FROM python:3.12-slim

# No bytecode litter, no interaction, unbuffered stdio (a buffered stdio server looks hung).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NAVIG_CONFIG_DIR=/data \
    NAVIG_DATA_DIR=/data \
    NAVIG_SKIP_ONBOARDING=1 \
    NAVIG_NO_TELEMETRY=1 \
    NAVIG_MCP_EXPOSURE=remote \
    HOME=/home/navig

# Exact pin, bumped with every release: a floating floor can change the served MCP
# surface between rebuilds with no diff for the operator to approve (D2).
ARG NAVIG_VERSION=3.29.0

WORKDIR /app

# `navig` from PyPI — the same artifact a user installs, so the container cannot drift
# from the documented behaviour. Non-root after install; /data is the only writable state.
RUN pip install "navig==${NAVIG_VERSION}" \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin navig \
    && mkdir -p /data \
    && chown -R navig:navig /data /app

# State lives here: read-only root filesystems and `--read-only` runs still work.
VOLUME ["/data"]
USER navig

# Compact surface by default. NAVIG_MCP_EXPOSURE=remote makes S2 real inside the
# container: dangerous actions are denied unless the operator explicitly sets
# mcp.remote_dangerous=approve in /data. For a local, trusted run override the CMD:
#   docker run -i --rm <image> --exposure local
ENTRYPOINT ["navig", "mcp", "serve", "--transport", "stdio", "--profile", "default"]
CMD ["--exposure", "remote"]
