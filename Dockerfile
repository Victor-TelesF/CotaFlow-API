# syntax=docker/dockerfile:1
FROM python:3.13.18-alpine3.24@sha256:1e1f6334529d1fb26a2ef619d7cffb30a7c7d7cc1cc7338ebee283a58d1aeaca

COPY --from=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app
# A base Alpine reduz os componentes do SO; atualizações vêm do repositório oficial.
RUN apk upgrade --no-cache \
    && python -m pip uninstall --yes pip \
    && rm -rf /usr/local/lib/python3.13/ensurepip
COPY pyproject.toml uv.lock .python-version ./
# CA opcional para builds em ambientes com proxy TLS.
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    uv sync --locked --no-dev --no-cache --no-build && adduser -D -u 1000 app

COPY --chown=app:app main.py alembic.ini LICENSE ./
COPY --chown=app:app domain/ domain/
COPY --chown=app:app application/ application/
COPY --chown=app:app infrastructure/ infrastructure/
COPY --chown=app:app presentation/ presentation/
COPY --chown=app:app alembic/ alembic/

USER app
EXPOSE 8000
# Migrate the configured PostgreSQL database before opening HTTP. On failure,
# do not start the server; exec makes Uvicorn receive shutdown signals as PID 1.
# Empty by default: trust only explicitly verified proxy peers, never arbitrary XFF.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port \"${PORT:-8000}\" --workers 1 --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-}\""]
