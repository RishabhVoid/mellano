# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.13.6 AS uv

FROM node:24-bookworm-slim AS web-build
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/tsconfig.json web/vite.config.ts web/index.html ./
COPY web/src ./src
COPY web/scenes ./scenes
RUN npm run build

FROM python:3.11-slim-bookworm AS app-base
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_NO_ENV_FILE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/otec-venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OTEC_ENABLE_LOCAL_COMPUTE=0 \
    OTEC_WATCH_EXPORTS=0 \
    OMP_NUM_THREADS=2 \
    OPENBLAS_NUM_THREADS=2 \
    MKL_NUM_THREADS=2 \
    VECLIB_MAXIMUM_THREADS=2 \
    NUMEXPR_NUM_THREADS=2 \
    PATH=/opt/otec-venv/bin:$PATH
WORKDIR /app
# rasterio/GDAL wheels still need the OS C libraries; slim Python images omit them.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project \
    && uv pip install --python /opt/otec-venv/bin/python 'psycopg[binary]==3.3.6'
COPY sim ./sim
COPY --from=web-build /build/web/dist ./web/dist
COPY web/public/assets/vehicles ./web/public/assets/vehicles
RUN groupadd --gid 10001 otec \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /var/lib/otec otec \
    && mkdir -p /app/web/public/data /app/data/cache/areas /app/data/derived /app/runs /app/logs /var/lib/otec \
    && chown -R 10001:10001 /app/data/cache /app/logs /var/lib/otec
USER 10001:10001
EXPOSE 5173
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5173/api/health', timeout=2).close()"]
ENTRYPOINT ["python", "-m", "sim.launch"]
CMD ["--host", "0.0.0.0", "--port", "5173", "--no-build"]

# Explicit optional target. Installs public tooling only, never accounts or keys.
FROM app-base AS runtime-gemini
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg -o /tmp/otec-cloud-key.asc \
    && gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg /tmp/otec-cloud-key.asc \
    && rm /tmp/otec-cloud-key.asc
RUN printf '%s\n' 'deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main' > /etc/apt/sources.list.d/google-cloud-sdk.list \
    && apt-get update \
    && CLOUDSDK_SKIP_PY_COMPILATION=1 apt-get install -y --no-install-recommends google-cloud-cli \
    && rm -rf /var/lib/apt/lists/*
USER 10001:10001

# Immutable Cloud Run demo. The runtime directory is assembled from explicit
# published assets; it never contains environment files or developer credentials.
FROM app-base AS runtime-cloud
USER root
COPY --chown=10001:10001 runtime/ /app/
ENV OTEC_FROZEN_DEMO=1
USER 10001:10001

# Default target has no Google CLI or credentials and starts only the web/API.
FROM app-base AS runtime
