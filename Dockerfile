# Hosted deployment of the Instantly MCP server (streamable-http transport).
#
# The server refuses to start on an HTTP transport unless MCP_AUTH_TOKEN and
# PUBLIC_URL are set -- see src/instantly_mcp/auth.py. Set those, plus
# INSTANTLY_API_KEY, as secrets in the host's dashboard. Never bake them in.
FROM python:3.12-slim

WORKDIR /app

# Install deps first so dependency layers cache across code edits.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Hosting platforms inject $PORT; auth.py reads it. 8000 is only the local default.
ENV TRANSPORT=streamable-http \
    HOST=0.0.0.0 \
    PORT=8000

# Drop root: nothing here needs to write to the filesystem except the audit log,
# which we point at a path the unprivileged user owns.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /var/log/instantly \
    && chown appuser /var/log/instantly
ENV INSTANTLY_AUDIT_LOG=/var/log/instantly/audit.log
USER appuser

EXPOSE 8000

CMD ["python", "-m", "instantly_mcp.server"]
