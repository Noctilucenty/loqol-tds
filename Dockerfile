# Two stages: build the SPA with Node, then serve everything from one Python
# process. Render's Python runtime does not ship Node, and pinning the toolchain
# here means the image builds identically on Render, Fly, or a laptop.

FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build            # outputs to /app/static via vite.config.ts


FROM python:3.12-slim
WORKDIR /srv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY data/ ./data/
COPY scripts/ ./scripts/

# The SPA build lands where FastAPI mounts its static files.
COPY --from=web /app/static ./app/static

# Run unprivileged.
RUN useradd --create-home --uid 10001 loqol && chown -R loqol:loqol /srv
USER loqol

EXPOSE 8000
# --proxy-headers: Render terminates TLS at its edge and forwards
# X-Forwarded-Proto. Without this, request.base_url reports http, and the seller
# link the agent copies is a plain-http URL to a legal document.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
