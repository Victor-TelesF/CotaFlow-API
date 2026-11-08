# Python API Template

Base enxuta para iniciar APIs com Python 3.13, FastAPI, SQLAlchemy síncrono,
Alembic, uv, Docker e PostgreSQL 17. Sem regras de negócio ou tabelas iniciais.
`GET /health` retorna `{"status":"ok"}`; indica que a API responde, sem consultar o banco.

## Começar um projeto

Pré-requisitos: Git, Docker com Compose v2 e, para executar fora do container,
[uv](https://docs.astral.sh/uv/getting-started/installation/).
O uv pode instalar o Python 3.13 automaticamente no ambiente local.

1. No GitHub, clique em **Use this template → Create a new repository** e escolha
   o nome, o proprietário e a visibilidade do novo projeto.
2. Clone o novo repositório e entre na pasta:

   ```sh
   git clone https://github.com/SEU_USUARIO/SEU_PROJETO.git
   cd SEU_PROJETO
   cp .env.example .env
   ```

3. Ajuste `name` e `description` no `pyproject.toml`, o título em `app/main.py`
   e este README. Depois de renomear o projeto, execute `uv lock` e versione o lockfile.
   Edite as variáveis do `.env` conforme necessário; seus valores de exemplo são
   fictícios e exclusivos para desenvolvimento. Não versione credenciais.

## Executar com Docker

```sh
docker compose up --build
```

O banco fica saudável antes da inicialização da API. O comando padrão executa
`alembic upgrade head` e só inicia o Uvicorn se a migração terminar com sucesso.
Isso funciona mesmo sem nenhuma revisão em `alembic/versions/`.

Em outro terminal:

```sh
curl http://127.0.0.1:8000/health
docker compose logs -f api db
```

API: `http://127.0.0.1:8000`; documentação interativa: `http://127.0.0.1:8000/docs`.
Para executar em segundo plano, use `docker compose up --build -d`.
Depois de alterar código Python, reconstrua a imagem com o mesmo comando.

```sh
docker compose down
```

O encerramento normal preserva os dados no volume `postgres_data` do projeto
Compose. **`docker compose down -v` apaga esse volume e seus dados**.
Cada pasta de projeto recebe, por padrão, seu próprio projeto Compose e volume;
use `docker compose -p nome-unico ...` se precisar distinguir pastas de mesmo nome.

### Endereços e portas

| Execução da API | Endereço do banco |
| --- | --- |
| No container, dentro da rede Compose | `db:5432` |
| No host, fora do Docker | `127.0.0.1:55432` |

O Compose define `DB_HOST=db` e `DB_PORT=5432` para a API, independentemente dos
valores locais do `.env`. As portas são publicadas apenas em `127.0.0.1`.
`DB_HOST_PORT` muda a porta do banco no host (padrão `55432`, evitando disputar
`5432` com um PostgreSQL instalado); `API_HOST_PORT` muda a da API (padrão `8000`).
Se mudar `DB_HOST_PORT`, ajuste também `DB_PORT` para executar a API no host.
Atualize as URLs dos comandos se mudar `API_HOST_PORT`.

## Executar a API localmente com uv

Com o `.env` criado e `DB_HOST=127.0.0.1`, `DB_PORT=55432`:

```sh
# Se a API do Compose estiver rodando, pare-a para liberar a porta 8000.
docker compose stop api
docker compose up -d --wait db
uv sync --locked
uv run --locked alembic upgrade head
uv run --locked uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Só o banco precisa estar no Docker. O ambiente `.venv` é gerenciado pelo uv.
Encerre o Uvicorn com `Ctrl+C` e o banco com `docker compose down`.

## Modelos e migrações

Declare futuros modelos herdando de `Base`, em `app/database.py`.
Por exemplo, ao criar `app/models.py`, use `from app.database import Base` nesse
módulo e adicione `from app import models` em `alembic/env.py`.
O import deve ocorrer antes de usar `Base.metadata`: é ele que registra as tabelas
para o autogenerate. Não use `create_all` na inicialização.

Com os modelos importados, gere a primeira migração no container:

```sh
docker compose run --build --rm --user "$(id -u):$(id -g)" api alembic revision --autogenerate -m "cria tabelas iniciais"
```

Esse comando é para um shell POSIX (Linux, macOS ou WSL). `--user` usa o UID/GID
local para permitir a escrita sem criar arquivos com outro proprietário no Linux.
Se usar Docker **rootless**, substitua a opção por `--user 0:0`: o root do
container é mapeado para seu usuário sem privilégios no host. Nesse caso:

```sh
docker compose run --build --rm --user 0:0 api alembic revision --autogenerate -m "cria tabelas iniciais"
```

O bind mount `./alembic:/app/alembic` faz a revisão aparecer no diretório local
`alembic/versions/`. `--build` inclui os novos modelos na imagem antes de gerar
a revisão. O comando substitui o início padrão da API, sem iniciar Uvicorn.

Revise o arquivo gerado, principalmente `upgrade()` e `downgrade()`: renomeações
e alterações de dados podem precisar de ajustes manuais. Sem modelos, a revisão
gerada será vazia. Depois aplique e confira:

```sh
docker compose run --rm api alembic upgrade head
docker compose run --rm api alembic current
docker compose up --build -d
```

Para gerar e aplicar pelo ambiente local, usando o banco do Docker:

```sh
uv run --locked alembic revision --autogenerate -m "cria tabelas iniciais"
# Revise o arquivo antes de aplicar.
uv run --locked alembic upgrade head
uv run --locked alembic current
```

Versione os modelos, o import em `alembic/env.py` e os arquivos de migração.
Aplicação e Alembic compartilham o mesmo engine e a configuração de `app/config.py`,
com conexão montada por `URL.create()` e `pool_pre_ping=True`.

## Adaptar para outro banco relacional

Troque ou instale o driver necessário com uv, atualize `DB_DRIVER` para o dialeto
do SQLAlchemy e ajuste `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` e `DB_PASSWORD`.
Adapte também o serviço `db` do Compose: imagem, variáveis, volume, healthcheck,
porta interna e as configurações da API. Alguns bancos, como SQLite, exigem
ajustar os argumentos de `URL.create()` e as opções do engine.
Revise tipos e operações das migrações para o novo dialeto; uma migração não é
automaticamente portável entre todos os bancos. Execute `uv lock` após editar
dependências manualmente e versione `pyproject.toml` e `uv.lock`.

## CI e versões futuras

O único workflow roda em `push` e `pull_request`: instala dependências com
`uv sync --locked`, valida o Compose com valores fictícios e constrói a imagem.
Não exige secrets. Python 3.13 é usado no `.python-version`, no projeto, na imagem
e na CI; as versões de dependências resolvidas ficam em `uv.lock`.

Esta versão tem somente a branch `main`. Variantes futuras podem ficar em outros
repositórios template ou em branches deste repositório. O fluxo padrão
**Use this template** copia a branch padrão ou, marcando **Include all branches**,
todas as branches; ele não oferece escolher uma única branch alternativa.
Para usar só uma variante de outra branch, peça ao Codex que copie os arquivos
dela para um novo repositório com histórico próprio.
