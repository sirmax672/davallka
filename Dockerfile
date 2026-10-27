FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=80
WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir --upgrade pip==26.2.2 \
    && pip install --no-cache-dir -r requirements.txt \
    && groupadd -g 10001 app && useradd -u 10001 -g app -M app \
    && mkdir /app/uploads /app/state && chown app:app /app/state
COPY app.py storage.py mcp_server.py oauth_server.py oidc_sessions.py asgi_server.py ./
COPY templates ./templates
COPY static ./static
USER 10001:10001
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:80/healthz',timeout=3)"]
# ASGI combines the Flask UI with the official MCP SDK Streamable HTTP routes.
CMD ["uvicorn", "asgi_server:app", "--host", "0.0.0.0", "--port", "80", "--workers", "1", "--limit-concurrency", "8", "--no-server-header"]

FROM runtime AS test
USER root
RUN pip install --no-cache-dir pytest==9.1.1
COPY tests ./tests
COPY pyproject.toml .
USER 10001:10001
CMD ["python", "-m", "pytest", "-q", "-o", "cache_dir=/tmp/pytest-cache"]

FROM runtime AS production
