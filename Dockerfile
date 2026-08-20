FROM node:20-alpine AS frontend-builder

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS builder

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

WORKDIR /build
RUN python -m venv "${VIRTUAL_ENV}"
COPY pyproject.toml constraints-langchain.txt README.md ./
COPY src/ src/
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -c constraints-langchain.txt \
       ".[dashboard,postgres-checkpoint]"

FROM python:3.12-slim AS runtime

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"
ENV PYTHONUNBUFFERED=1
ENV FRONTEND_DIST_DIRECTORY=/app/frontend/dist

RUN groupadd --system snoc \
    && useradd --system --gid snoc --home-dir /app --create-home snoc

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY alembic.ini ./
COPY alembic/ alembic/
COPY scripts/ scripts/
COPY dashboard.py ./
COPY src/ src/
COPY --from=frontend-builder /build/frontend/dist /app/frontend/dist
RUN mkdir -p /app/outputs /app/var \
    && chown -R snoc:snoc /app/outputs /app/var /app/frontend

USER snoc
CMD ["snoc-agent", "worker", "run"]
