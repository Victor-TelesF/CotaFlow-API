"""Cadastro, login e identidade; banco e segurança chegam pelos contratos.

O HTTP fornece email validado e canônico e senha com os limites do contrato.
Hash e verificação de senha ocorrem sem manter conexão de banco ocupada.
"""

from collections.abc import Callable

from application.exceptions import InvalidCredentials, InvalidToken
from application.ports import PasswordHasher, TokenService, UnitOfWork
from application.types import User


def register(
    email: str,
    password: str,
    uow_factory: Callable[[], UnitOfWork],
    passwords: PasswordHasher,
) -> User:
    """Persiste somente o hash; unicidade do banco também protege a corrida.

    EmailAlreadyRegistered é traduzido pelo adaptador na constraint exata.
    Qualquer falha na escrita sai do contexto com rollback, sem commit parcial.
    """
    user = User(email=email, password_hash=passwords.hash(password))
    with uow_factory() as uow:
        uow.users.add(user)
        uow.commit()
    return user


def login(
    email: str,
    password: str,
    uow_factory: Callable[[], UnitOfWork],
    passwords: PasswordHasher,
    tokens: TokenService,
) -> str:
    """Fecha a leitura antes do Argon2 e rejeita email/senha com o mesmo erro."""
    with uow_factory() as uow:
        user = uow.users.get_by_email(email)
    matches = passwords.verify(password, user.password_hash if user else None)
    if user is None or not matches:
        raise InvalidCredentials
    return tokens.issue(user.id)


def resolve_identity(
    token: str, uow_factory: Callable[[], UnitOfWork], tokens: TokenService
) -> User:
    """Confere assinatura/claims antes do banco e exige usuário ainda existente.

    O UUID vem do token validado, nunca de parâmetros informados pelo cliente.
    As futuras consultas da carteira devem usar esta identidade como dono.
    """
    user_id = tokens.verify(token)
    with uow_factory() as uow:
        user = uow.users.get(user_id)
    if user is None:
        raise InvalidToken
    return user
