FROM node:20-bookworm-slim AS frontend-builder

WORKDIR /frontend
COPY pyproject.toml /pyproject.toml
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_NAME=release-controller \
    APP_ENV=production \
    LOG_LEVEL=INFO \
    DATABASE_URL=sqlite:////data/release.db

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY --from=frontend-builder /app/static ./app/static

RUN mkdir -p /data

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]
