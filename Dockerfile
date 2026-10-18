# NAVIG's MCP server in a container.
#
# Purpose (docs/reference/listing-submissions.md, L3): an MCP directory can introspect a server
# without a Python environment on the host — it needs a Dockerfile that starts the server and
# answers introspection. This is that Dockerfile, and it is also the honest way to run NAVIG's
# MCP tools on a machine where you do not want the kernel installed globally.
#
# What it deliberately does NOT do: mount the host's vault, run the gateway, or hold any key.
# `navig mcp serve` over stdio answers the client that spawned it; the client owns the transport.
FROM python:3.12-slim

# No bytecode litter, no interaction, unbuffered stdio (a buffered stdio server looks hung).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NAVIG_CONFIG_DIR=/data \
    NAVIG_DATA_DIR=/data

WORKDIR /app

# `navig` from PyPI — the same artifact a user installs, so the container cannot drift from the
# documented behaviour. Pin the major floor, never a patch: the MCP surface is additive.
RUN pip install "navig>=3.27,<4"

# State lives here: read-only root filesystems and `--read-only` runs still work.
VOLUME ["/data"]

# Introspection needs the server to speak MCP on stdio immediately.
ENTRYPOINT ["navig", "mcp", "serve", "--transport", "stdio"]
