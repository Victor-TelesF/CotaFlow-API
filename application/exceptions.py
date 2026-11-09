"""Falhas esperadas da aplicação, sem detalhes de banco ou credenciais."""


class EmailAlreadyRegistered(Exception):
    """O email canônico já pertence a uma identidade persistida."""


class InvalidCredentials(Exception):
    """Email inexistente ou senha incorreta; não distingue os dois ao cliente."""


class InvalidToken(Exception):
    """Token rejeitado ou identidade que já não existe no banco."""


class AssetAlreadyRegistered(Exception):
    """O ticker canônico já existe no catálogo compartilhado."""


class CatalogAssetNotFound(Exception):
    """A operação referencia um ativo ausente do catálogo local."""


class ValuationRateLimited(Exception):
    """Orçamento de avaliações do usuário esgotado temporariamente."""

    def __init__(self, retry_after: int):
        super().__init__("Valuation rate limited")
        self.retry_after = retry_after


class ValuationTooLarge(Exception):
    """Avaliação excede o número configurado de posições abertas."""


class TransactionRateLimited(Exception):
    """Orçamento de tentativas de compra/venda esgotado temporariamente."""

    def __init__(self, retry_after: int):
        super().__init__("Transaction rate limited")
        self.retry_after = retry_after


class QuotesUnavailable(Exception):
    """Falha segura; não carrega corpo, URL, header ou exceção do provedor."""

    def __init__(
        self,
        tickers: set[str],
        reason: str = "unavailable",
        retry_after: int | None = None,
    ):
        super().__init__("Quotes unavailable")
        self.tickers = tuple(sorted(tickers))
        self.reason = reason
        self.retry_after = retry_after


class AuthenticationLimited(Exception):
    """Admissão esgotada, sem carregar identidade ou entrada recebida."""

    def __init__(self, retry_after: int):
        self.retry_after = max(1, min(600, retry_after))
        super().__init__("Authentication budget exhausted")
