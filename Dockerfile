# syntax=docker/dockerfile:1
FROM python:3.15.0-slim-trixie@sha256:f5b92f2a669a4c347044f29367f31983280b648250ca94dea66f98c53bc87e58

COPY --from=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app
# Atualiza a biblioteca de compressão da base e remove instaladores Python sem
# consumidor no runtime (incluindo dependências vendorizadas e ensurepip).
RUN apt-get update \
    && apt-get install --yes --no-install-recommends --only-upgrade zlib1g \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall --yes pip \
    && rm -rf /usr/local/lib/python3.13/ensurepip
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
# Empty by default: trust only explicitly verified proxy peers, never arbitrary XFF.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port \"${PORT:-8000}\" --workers 1 --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-}\""]
