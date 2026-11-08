# CotaFlow API

API em desenvolvimento para acompanhar uma carteira de **ações brasileiras e FIIs**, com registro de operações e cotações automáticas.

O projeto parte de um domínio financeiro em Python e segue uma proposta de Clean Architecture, com responsabilidades separadas e poucas abstrações. O recorte inicial usa BRL e uma carteira por usuário.

## Estado atual

**Etapa 1 — domínio reduzido e validado localmente.**

- Template com FastAPI, PostgreSQL, configuração por ambiente, Alembic, Docker Compose e workflow de CI.
- Domínio limitado a ações brasileiras e FIIs, com histórico, preço médio, custo e cálculos de avaliação por preços fornecidos.
- Camadas `domain`, `application`, `infrastructure` e `presentation` organizadas; aplicação, segurança e cotações ainda aguardam implementação.
- Dependências definidas no `pyproject.toml`, com versões exatas no `uv.lock`.
- Endpoint `GET /health` disponível; verifica a resposta da API, sem consultar o banco.

Próximos passos: implementar persistência, autenticação, rotas de operações e integração de cotações. Cadastro/login, compras/vendas e avaliação **ainda não estão disponíveis na API**. Os arquivos de Docker/CI estão preparados; isso não equivale a uma execução remota aprovada.

## Stack

| Responsabilidade | Bibliotecas |
|---|---|
| API e servidor | FastAPI, Uvicorn |
| Schemas e configuração | Pydantic/email, Pydantic Settings |
| Persistência | SQLAlchemy síncrono, Psycopg, PostgreSQL 17 |
| Migrations | Alembic |
| Cliente HTTP | HTTPX2 |
| Segurança planejada | PyJWT, pwdlib/Argon2 |
| Desenvolvimento | pytest, pytest-cov, Ruff |

Python **3.13** e dependências gerenciadas pelo **uv**. A inclusão de uma biblioteca não significa que a funcionalidade correspondente já foi implementada.

## Executar localmente

Pré-requisitos: Docker com Compose v2. Na raiz do projeto, crie o `.env` a partir do exemplo somente se ele ainda não existir e ajuste os valores para seu ambiente:

```sh
[ -f .env ] || cp .env.example .env
docker compose up --build
```

Em outro terminal:

```sh
curl http://127.0.0.1:8000/health
```

Resposta esperada:

```json
{"status": "ok"}
```

Documentação interativa: `http://127.0.0.1:8000/docs`. A porta padrão da API é `8000`; o PostgreSQL fica em `127.0.0.1:55432` no host e `db:5432` dentro do Compose. O início do container aplica as migrations antes de subir a API; ainda não existem revisões de negócio.

Para encerrar mantendo o volume do banco:

```sh
docker compose down
```

## Ambiente Python

Com o uv instalado:

```sh
uv sync --locked
uv lock --check
```

As ferramentas de desenvolvimento ficam no grupo `dev`. A imagem usa `uv sync --locked --no-dev`. Para verificar o domínio e o lint:

```sh
uv run --locked python -m pytest -q
uv run --locked ruff check .
```

A suíte atual verifica regras financeiras sem banco ou rede. A entrada da API é `main:app`. PostgreSQL, migrations de negócio, provedor de cotações e Docker/CI ainda precisam de validação integrada.

Credenciais devem permanecer fora do repositório e das imagens. O arquivo `.env.example` contém apenas configuração fictícia de desenvolvimento.
