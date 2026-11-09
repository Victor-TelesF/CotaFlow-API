# Guia de uso da CotaFlow API

Este guia descreve como autenticar, chamar os endpoints e interpretar as respostas da API. A especificação gerada pelo serviço também está disponível no [Swagger](https://cotaflow-api.onrender.com/docs) e em [OpenAPI JSON](https://cotaflow-api.onrender.com/openapi.json).

## Endereço e formato

Na hospedagem pública, use `https://cotaflow-api.onrender.com` como endereço base. Em uma instalação própria, substitua-o pelo endereço da sua instância. Os endpoints de produto usam o prefixo `/api/v1`; a API recebe e responde JSON. Datas usam `YYYY-MM-DD` e valores decimais são enviados e retornados como **strings**, preservando precisão.

```sh
export API_URL="https://cotaflow-api.onrender.com"
```

## Fluxo recomendado

1. Registre a conta uma vez.
2. Faça login e guarde o `access_token` em um gerenciador seguro durante o uso.
3. Consulte o catálogo para confirmar que o ticker está cadastrado.
4. Registre as operações em ordem histórica coerente.
5. Consulte posições, avaliação e histórico com o mesmo token.

O catálogo é compartilhado entre contas. As operações e consultas de carteira são isoladas pela identidade do token. A API não recebe `user_id` nos dados da operação: o titular é determinado pelo token.

## Autenticação

### Criar uma conta

`POST /api/v1/auth/register`

A senha deve ter entre 8 e 128 caracteres. O email é validado e armazenado em formato canônico (sem distinção entre maiúsculas/minúsculas).

```sh
curl -i "$API_URL/api/v1/auth/register" \
  -H 'Content-Type: application/json' \
  -d '{"email":"ana@example.com","password":"senha-forte-de-exemplo"}'
```

Resposta `201 Created`:

```json
{"id":"9bf2d4e1-6a3c-4a8b-a75d-1c9a370d04d2","email":"ana@example.com"}
```

O exemplo usa dados fictícios. A resposta não contém senha nem token.

### Fazer login

`POST /api/v1/auth/login`

```sh
curl -i "$API_URL/api/v1/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"email":"ana@example.com","password":"senha-forte-de-exemplo"}'
```

Resposta `200 OK`:

```json
{"access_token":"<token JWT>","token_type":"bearer"}
```

O token tem validade configurável (30 minutos por padrão). Envie-o no cabeçalho `Authorization` das rotas protegidas. Não o inclua na URL nem o publique em logs ou repositórios.

```sh
export TOKEN="<token JWT recebido no login>"
curl "$API_URL/api/v1/assets" -H "Authorization: Bearer $TOKEN"
```

## Endpoints

| Método | Endpoint | Autenticação | Uso |
| --- | --- | :---: | --- |
| `GET` | `/health` | Não | Confirma que o processo responde. Não verifica banco nem provedor de cotações. |
| `POST` | `/api/v1/auth/register` | Não | Cria uma conta. |
| `POST` | `/api/v1/auth/login` | Não | Emite um token Bearer. |
| `GET` | `/api/v1/assets` | Sim | Lista o catálogo compartilhado, ordenado por ticker. |
| `POST` | `/api/v1/portfolio/transactions` | Sim | Registra uma compra ou venda. |
| `GET` | `/api/v1/portfolio/transactions` | Sim | Lista o histórico da conta. |
| `GET` | `/api/v1/portfolio/positions` | Sim | Retorna posições abertas e custo total. |
| `GET` | `/api/v1/portfolio/valuation` | Sim | Consulta cotação e calcula a avaliação completa. |

As rotas autenticadas exigem `Authorization: Bearer <token>`. Tokens ausentes, inválidos ou expirados recebem `401 Unauthorized`.

### Catálogo de ativos

`GET /api/v1/assets?limit=50&offset=0`

`limit` aceita de 1 a 100 (padrão 50); `offset` começa em 0 e tem limite operacional de 10.000. A resposta contém `items`, `limit` e `offset`:

```json
{
  "items": [
    {"ticker":"PETR4","name":"Petrobras PN","type":"stock"},
    {"ticker":"HGLG11","name":"CSHG Logística","type":"fii"}
  ],
  "limit": 50,
  "offset": 0
}
```

Os itens acima ilustram o formato; os ativos efetivamente disponíveis dependem do catálogo da instância. Um ticker precisa estar cadastrado para que uma operação seja aceita.

### Registrar compra ou venda

`POST /api/v1/portfolio/transactions`

Envie quantidade e preço como strings decimais, sem separador de milhar. `type` aceita `buy` ou `sell`. `date` é a data da operação, não a data de recebimento da requisição.

```sh
curl -i "$API_URL/api/v1/portfolio/transactions" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"ticker":"PETR4","quantity":"10","price":"37.25","type":"buy","date":"2026-11-09"}'
```

Resposta `201 Created`:

```json
{
  "id":"2a861f70-2394-4948-8f5e-e27d6877de8e",
  "ticker":"PETR4",
  "quantity":"10.00000000",
  "price":"37.25000000",
  "type":"buy",
  "date":"2026-11-09"
}
```

Use somente tickers presentes no catálogo. Uma venda precisa ser compatível com o saldo ao reconstruir o histórico completo, inclusive para operações retroativas. Erros de saldo não gravam uma operação inválida. Campos desconhecidos no corpo da operação são rejeitados.

### Consultar histórico

`GET /api/v1/portfolio/transactions?limit=50&offset=0`

Usa a mesma paginação do catálogo. Cada item segue o formato da resposta de registro; a ordenação considera a data e uma sequência estável de registro para operações no mesmo dia. A paginação limita apenas a resposta, não o histórico usado para validar uma nova operação.

### Consultar posições

`GET /api/v1/portfolio/positions`

A resposta contém as posições abertas e `total_cost`. Carteira vazia retorna `positions: []` e custo zero. Valores decimais são strings:

```json
{
  "positions": [
    {
      "ticker":"PETR4",
      "name":"Petrobras PN",
      "type":"stock",
      "quantity":"10",
      "average_price":"37.25",
      "cost":"372.5"
    }
  ],
  "total_cost":"372.5"
}
```

### Consultar avaliação

`GET /api/v1/portfolio/valuation`

A resposta é completa: se faltar cotação para qualquer posição, a API retorna erro em vez de apresentar um total parcial. Cada posição inclui os dados de custo, a cotação e os indicadores de mercado. `unrealized_return` é uma razão decimal (`0.20` equivale a 20%) e pode ser `null` quando não há denominador para calculá-la.

```json
{
  "currency":"BRL",
  "positions":[
    {
      "ticker":"PETR4",
      "name":"Petrobras PN",
      "type":"stock",
      "quantity":"10",
      "average_price":"37.25",
      "cost":"372.5",
      "quote":{
        "ticker":"PETR4",
        "price":"40.00",
        "currency":"BRL",
        "source":"brapi",
        "quoted_at":"2026-11-09T17:00:00Z",
        "fetched_at":"2026-11-09T17:01:00Z",
        "cached":false
      },
      "market_value":"400.00",
      "unrealized_pnl":"27.50"
    }
  ],
  "total_cost":"372.5",
  "total_market_value":"400.00",
  "total_unrealized_pnl":"27.50",
  "unrealized_return":"0.07382550335570469798657718121"
}
```

Os números e horários são apenas ilustrativos. `quoted_at` pode ser `null`; `fetched_at` indica quando a API buscou a cotação. `cached` informa se a resposta veio do cache. Cotações podem estar atrasadas e não alteram o preço ou a data das operações registradas.

## Erros e limites

Erros usam um objeto JSON com `code` e `message`; erros de validação podem também incluir `details` com localização e tipo, sem repetir valores enviados pelo cliente.

| HTTP | Quando ocorre |
| :---: | --- |
| `401` | Bearer ausente/inválido/expirado ou credenciais de login incorretas. |
| `404` | Ticker da operação não encontrado no catálogo. |
| `409` | Email já cadastrado ou operação incompatível com o saldo histórico. |
| `413` | Corpo da requisição excede 16 KiB. |
| `422` | JSON/campos inválidos, valor financeiro inválido, paginação fora do limite ou posições acima do máximo configurado para avaliação. |
| `429` | Limite de requisições ou de concorrência atingido. Consulte `Retry-After` antes de repetir. |
| `503` | Banco indisponível ou cotação incompleta/indisponível. Avaliação indisponível não inclui totais parciais. |

As quotas são configuráveis e mantidas por processo; não representam um SLA nem um limite mensal do provedor. A instância pode retornar `429` com `Retry-After`. Em `503` por cotação, confira `tickers` e `reason`; um identificador `X-Request-ID` pode ser usado para correlacionar suporte sem enviar o token.

## Referência interativa

Para ver os schemas e respostas publicados pela instância, acesse o [Swagger UI] ou baixe o [documento OpenAPI]. A interface permite montar chamadas; para rotas protegidas, use o botão **Authorize** e informe o token Bearer.

[Swagger UI]: https://cotaflow-api.onrender.com/docs
[documento OpenAPI]: https://cotaflow-api.onrender.com/openapi.json
