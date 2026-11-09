# CotaFlow API

API REST para registrar compras e vendas de ações brasileiras e FIIs e consultar carteiras em BRL. Cada conta tem seu próprio histórico, posições e avaliações; o catálogo de ativos disponível é compartilhado.

O serviço oferece cadastro e login com JWT, consulta paginada do catálogo e do histórico, registro de operações, cálculo de posições abertas e avaliação com cotações da brapi. As cotações são uma fotografia de mercado com cache e podem ter atraso; os preços e as datas informados nas operações permanecem no histórico.

## Comece a usar

1. Crie uma conta em `POST /api/v1/auth/register` e obtenha um token em `POST /api/v1/auth/login`.
2. Envie o token como `Authorization: Bearer <token>` nas rotas protegidas.
3. Consulte `GET /api/v1/assets` para escolher um ativo disponível antes de registrar operações.
4. Registre compras ou vendas em `POST /api/v1/portfolio/transactions`; depois consulte posições, avaliação e histórico.

Leia o [guia de uso e referência de endpoints](API.md) para exemplos de requisição, respostas, autenticação, paginação e erros.

## Acesso

- [Swagger interativo](https://cotaflow-api.onrender.com/docs)
- [Especificação OpenAPI](https://cotaflow-api.onrender.com/openapi.json)
- [Healthcheck](https://cotaflow-api.onrender.com/health)
- [Frontend](https://cota-flow-web.vercel.app)

## Tecnologias

| Área | Tecnologias |
| --- | --- |
| API | Python 3.13 · FastAPI 0.162.0 · Pydantic |
| Banco de dados | PostgreSQL 17 · SQLAlchemy · Alembic · Psycopg |
| Segurança e cotações | PyJWT · Argon2 · HTTPX2 · brapi |
| Desenvolvimento e entrega | uv · pytest · Ruff · Docker Compose · GitHub Actions · Render |

Projeto de portfólio voltado a APIs backend para controle de carteira. O catálogo inicial e a validação operacional da hospedagem seguem em evolução; consulte o Swagger para o contrato publicado no ambiente.

Licença [MIT](LICENSE).
