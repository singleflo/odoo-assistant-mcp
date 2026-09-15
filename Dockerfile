FROM python:3.12-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir ".[remote]"

# Non-root runtime; /data is the only writable path (ODOO_MCP_DATA_DIR).
RUN useradd --create-home mcp \
    && mkdir -p /data \
    && chown mcp:mcp /data

ENV ODOO_MCP_DATA_DIR=/data \
    ODOO_REMOTE_HOST=0.0.0.0

USER mcp
EXPOSE 8000

# python:3.12-slim has no curl/wget; urllib is already there.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

CMD ["odoo-assistant-remote"]
