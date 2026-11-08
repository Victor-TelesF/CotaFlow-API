"""Falhas esperadas da aplicação, sem detalhes de banco ou credenciais."""


class EmailAlreadyRegistered(Exception):
    """O email canônico já pertence a uma identidade persistida."""


class InvalidCredentials(Exception):
    """Email inexistente ou senha incorreta; não distingue os dois ao cliente."""


class InvalidToken(Exception):
    """Token rejeitado ou identidade que já não existe no banco."""
