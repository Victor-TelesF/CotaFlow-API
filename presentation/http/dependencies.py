"""Resolução de identidade compartilhada pelas rotas protegidas."""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from application.auth import resolve_identity
from application.exceptions import InvalidToken
from application.types import User

bearer = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    """Aceita somente Bearer válido e usuário existente; ausência resulta em 401."""
    if credentials is None:
        raise InvalidToken
    return resolve_identity(
        credentials.credentials,
        request.app.state.uow_factory,
        request.app.state.tokens,
    )


CurrentUser = Annotated[User, Depends(get_current_user)]
