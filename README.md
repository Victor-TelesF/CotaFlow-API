# CotaFlow API

**Acompanhe posições, custo e valor de mercado da sua carteira de ações e FIIs.**

API REST em Python para registrar compras e vendas de ativos brasileiros e acompanhar a evolução da carteira. Cada usuário tem acesso autenticado às próprias operações, posições e avaliações em reais.

[Swagger UI](https://cotaflow-api.onrender.com/docs) · [OpenAPI](https://cotaflow-api.onrender.com/openapi.json) · [Healthcheck](https://cotaflow-api.onrender.com/health)

## Funcionalidades

- **Cadastro e login:** acesso individual com autenticação JWT.
- **Catálogo de ativos:** consulta de ações brasileiras e fundos imobiliários.
- **Compras e vendas:** registro de quantidade, preço e data de cada operação.
- **Posições consolidadas:** saldo disponível, preço médio e custo dos ativos em carteira.
- **Avaliação de mercado:** cotações da brapi, valor atual e resultado não realizado.
- **Histórico individual:** consulta paginada das operações de cada usuário.

O fluxo de uso é **cadastro → login → consulta ao catálogo → compra → posições → avaliação → venda → histórico**.

## Um exemplo prático

Duas compras de **10 unidades a R$ 30** e **10 a R$ 40**, seguidas de uma venda de **5 unidades**, deixam a posição abaixo. Com uma cotação ilustrativa de **R$ 42**:

| Quantidade | Preço médio | Custo aberto | Valor de mercado | Resultado não realizado |
| ---: | ---: | ---: | ---: | ---: |
| 15 | R$ 35,00 | R$ 525,00 | R$ 630,00 | +R$ 105,00 (+20%) |

O histórico preserva os valores de cada operação. O resultado apresentado compara o valor de mercado com o custo da posição que permanece em carteira.

## Tecnologias

| Área | Tecnologias |
| --- | --- |
| API | Python 3.13, FastAPI, Pydantic |
| Banco de dados | PostgreSQL, SQLAlchemy, Alembic, Psycopg |
| Autenticação e cotações | PyJWT, Argon2, HTTPX2, brapi |
| Desenvolvimento e entrega | uv, pytest, Ruff, Docker, GitHub Actions, Render |

## Sobre o projeto

O CotaFlow é um projeto de portfólio voltado ao desenvolvimento backend com Python. Seu escopo atual reúne ações brasileiras e FIIs em BRL, com uma carteira por usuário e acompanhamento das operações informadas por ele.

As funcionalidades principais estão implementadas. O projeto segue em revisão e validação para publicação das melhorias; a integração com um frontend está prevista.
