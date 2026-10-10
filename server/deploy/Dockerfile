FROM node:24-bookworm-slim AS node

FROM node AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS api
ARG DEBIAN_MIRROR=https://deb.debian.org
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install -g pnpm@10.17.1
WORKDIR /app
COPY contracts/ contracts/
COPY ddl_text/ ddl_text/
COPY task_planning/ task_planning/
COPY server/ server/
ARG UV_INDEX_URL=https://pypi.org/simple
RUN --mount=from=ghcr.io/astral-sh/uv:0.13.0,source=/uv,target=/bin/uv \
    uv pip install --system --no-cache -r server/requirements.txt \
    -r ddl_text/media_requirements.txt -r ddl_text/teaching_requirements.txt
COPY crawler/ crawler/
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NIUMA_HOST=0.0.0.0 \
    NIUMA_DB=/data/niuma.sqlite3
RUN cd crawler && pnpm install --frozen-lockfile \
    && pnpm exec playwright install --with-deps chromium \
    && sed -i "s|${DEBIAN_MIRROR}|https://deb.debian.org|g" /etc/apt/sources.list.d/debian.sources \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home niuma \
    && mkdir -p /data /app/crawler/data \
    && chown -R niuma:niuma /data /app/crawler/data
USER niuma
EXPOSE 1332
CMD ["python", "server/app.py"]

FROM caddy:2-alpine AS web
COPY --from=frontend-build /build/frontend/dist /srv
COPY server/deploy/Caddyfile /etc/caddy/Caddyfile
