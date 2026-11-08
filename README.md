# CotaFlow API

API em desenvolvimento para acompanhar uma carteira de **ações brasileiras e FIIs**, com registro de operações e cotações automáticas.

O projeto parte de um domínio financeiro em Python e segue uma proposta de Clean Architecture, com responsabilidades separadas e poucas abstrações. O recorte inicial usa BRL e uma carteira por usuário.

## Estado atual

**Etapa 3 — cadastro e autenticação validados localmente.**

- Template com FastAPI, PostgreSQL, configuração por ambiente, Alembic, Docker Compose e workflow de CI.
- Domínio limitado a ações brasileiras e FIIs, com histórico, preço médio, custo e cálculos de avaliação por preços fornecidos.
- Persistência de usuários, ativos e transações, com migration inicial, repositórios e unidade de trabalho com commit explícito e rollback.
- Sessões por unidade de trabalho e pool encerrado no shutdown; configuração carregada na inicialização da API.
- Cadastro/login com email canônico, hash Argon2 e JWT HS256 com expiração; dependência de identidade preparada para as rotas protegidas.
- Dependências definidas no `pyproject.toml`, com versões exatas no `uv.lock`.
- Endpoint `GET /health` disponível; verifica a resposta da API, sem consultar o banco.

Disponíveis: `POST /api/v1/auth/register`, `POST /api/v1/auth/login` e `GET /health`. Próximos passos: catálogo, rotas de operações, posições e integração de cotações. Compras/vendas e avaliação **ainda não estão disponíveis na API**. Docker Compose da aplicação e CI ainda precisam de validação integrada.

## Stack

| Responsabilidade | Bibliotecas |
|---|---|
| API e servidor | FastAPI, Uvicorn |
| Schemas e configuração | Pydantic/email, Pydantic Settings |
| Persistência | SQLAlchemy síncrono, Psycopg, PostgreSQL 17 |
| Migrations | Alembic |
| Cliente HTTP | HTTPX2 |
| Autenticação | PyJWT, pwdlib/Argon2 |
| Desenvolvimento | pytest, pytest-cov, Ruff |

Python **3.13** e dependências gerenciadas pelo **uv**. A inclusão de uma biblioteca não significa que a funcionalidade correspondente já foi implementada.

## Executar localmente

Pré-requisitos: Docker com Compose v2. Na raiz do projeto, crie o `.env` a partir do exemplo somente se ele ainda não existir e ajuste os valores para seu ambiente. Defina `JWT_SECRET_KEY` com uma chave aleatória própria de pelo menos 32 caracteres; não use o valor fictício do exemplo. `JWT_ACCESS_TOKEN_MINUTES` vale 30 por padrão (limite: 1–1440). A chave é obrigatória no startup da API; o Alembic precisa somente da configuração de banco.

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

Documentação interativa: `http://127.0.0.1:8000/docs`. A porta padrão da API é `8000`; o PostgreSQL fica em `127.0.0.1:55432` no host e `db:5432` dentro do Compose. O início do container aplica a migration das três tabelas antes de subir a API.

Cadastro e login recebem JSON com `email` e `password`. O cadastro exige senha de 8–128 caracteres, preservada exatamente; o endereço inteiro é tratado sem distinção entre maiúsculas. Cadastro retorna somente `id`/`email`; login retorna `access_token`/`token_type`. Use o token no header `Authorization: Bearer <token>` das futuras rotas protegidas. Email duplicado retorna `409`, autenticação inválida `401` e entrada inválida `422`, sem ecoar senhas ou tokens nos erros. Não há refresh token ou recuperação de senha nesta etapa.

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

As ferramentas de desenvolvimento ficam no grupo `dev`. A imagem usa `uv sync --locked --no-dev`. Para executar a suíte sem banco/rede e o lint:

```sh
uv run --locked python -m pytest -q
uv run --locked ruff check .
```

Os testes de PostgreSQL são executados separadamente, em `integration_tests/`. Prepare um banco local **exclusivo e descartável**, com nome iniciado por `cotaflow_test_`, e informe a conexão explicitamente. Exemplo com valores fictícios:

```sh
TEST_DATABASE_DISPOSABLE=1 \
TEST_DATABASE_URL='postgresql+psycopg://test_user:test_password@127.0.0.1:55433/cotaflow_test_local' \
uv run --locked python -m pytest integration_tests -q
```

Cada teste cria e remove somente seu schema exclusivo. Configuração ausente ou incompatível causa falha; o banco da aplicação nunca é usado como fallback. Migrations, constraints, rollback, encerramento de conexões e autenticação HTTP foram exercitados com PostgreSQL 17 descartável, incluindo cadastros concorrentes com o mesmo email. A dependência de identidade é verificada por uma rota exclusiva dos testes; ainda não há rotas financeiras para validar seu isolamento. A entrada da API é `main:app`; provedor de cotações e Docker/CI da aplicação continuam pendentes.

Credenciais devem permanecer fora do repositório e das imagens. O arquivo `.env.example` contém apenas configuração fictícia de desenvolvimento.
