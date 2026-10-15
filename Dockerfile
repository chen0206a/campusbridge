# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.lock requirements-mysql.lock requirements-redis.lock ./
RUN python -m pip install --no-cache-dir -r requirements.lock -r requirements-mysql.lock -r requirements-redis.lock
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --no-deps . && groupadd --gid 10001 campusbridge && useradd --uid 10001 --gid 10001 --no-create-home campusbridge \
 && mkdir -p /var/lib/campusbridge/knowledge /run/cb-root /run/cb-app /run/cb-migrator /run/cb-redis \
 && chown -R 10001:10001 /var/lib/campusbridge /run/cb-root /run/cb-app /run/cb-migrator /run/cb-redis
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "campusbridge.deployment", "serve"]

FROM base AS dense
USER root
COPY requirements-rag.lock ./
RUN python -m pip install --no-cache-dir -r requirements-rag.lock
USER 10001:10001

FROM base AS runtime
