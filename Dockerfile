# syntax=docker/dockerfile:1
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
# CA opcional para builds em ambientes com proxy TLS.
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    uv sync --locked --no-dev --no-cache && useradd --create-home --uid 1000 app

COPY --chown=app:app main.py alembic.ini ./
COPY --chown=app:app domain/ domain/
COPY --chown=app:app application/ application/
COPY --chown=app:app infrastructure/ infrastructure/
COPY --chown=app:app presentation/ presentation/
COPY --chown=app:app alembic/ alembic/

USER app
EXPOSE 8000
# Migrate the configured PostgreSQL database before opening HTTP. On failure,
# do not start the server; exec makes Uvicorn receive shutdown signals as PID 1.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port \"${PORT:-8000}\" --workers 1"]
