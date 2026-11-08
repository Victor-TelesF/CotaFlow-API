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
