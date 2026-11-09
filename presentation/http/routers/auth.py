"""Duas rotas de autenticação; coordenação permanece na aplicação."""

from fastapi import APIRouter, Request, Response

from application.auth import login, register
from presentation.http.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from presentation.http.schemas.errors import AUTHENTICATION_ERROR, ErrorResponse

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=201,
    responses={
        409: {"model": ErrorResponse, "description": "Email already registered"}
    },
)
def register_user(body: RegisterRequest, request: Request) -> UserResponse:
    """Cria identidade e retorna apenas UUID/email; duplicata resulta em 409."""
    with request.app.state.auth_limits.admit(
        "register", request.client.host if request.client else None, str(body.email)
    ):
        user = register(
            str(body.email),
            body.password.get_secret_value(),
            request.app.state.uow_factory,
            request.app.state.passwords,
        )
    return UserResponse(id=user.id, email=user.email)


@router.post(
    "/login", response_model=TokenResponse, responses={401: AUTHENTICATION_ERROR}
)
def login_user(
    body: LoginRequest, request: Request, response: Response
) -> TokenResponse:
    """Autentica com JSON; não inclui token na URL nem persiste a senha."""
    with request.app.state.auth_limits.admit(
        "login", request.client.host if request.client else None, str(body.email)
    ):
        token = login(
            str(body.email),
            body.password.get_secret_value(),
            request.app.state.uow_factory,
            request.app.state.passwords,
            request.app.state.tokens,
        )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return TokenResponse(access_token=token)
