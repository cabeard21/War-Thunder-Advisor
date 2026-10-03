# syntax=docker/dockerfile:1
FROM --platform=$BUILDPLATFORM node:22.22.2-bookworm-slim AS frontend
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.11-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    WT_ADVISOR_DB=/data/wt-advisor.sqlite \
    WT_ADVISOR_BIND_ADDRESS=0.0.0.0 \
    WT_ADVISOR_PORT=8765
WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY --from=frontend /build/src/wt_advisor/web/static/ ./src/wt_advisor/web/static/
RUN pip install --no-cache-dir --upgrade "setuptools>=83" \
    && pip install --no-cache-dir . \
    && groupadd --gid 10001 advisor \
    && useradd --uid 10001 --gid advisor --no-create-home advisor \
    && mkdir /data && chown advisor:advisor /data
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('WT_ADVISOR_PORT','8765')+'/health',timeout=4).close()"
CMD ["wt-advisor", "dashboard"]
