"""Fronteira ASGI com corpo limitado e admissão por peer antes do parsing."""

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from application.exceptions import AuthenticationLimited

MAX_BODY_BYTES = 16 * 1024
AUTH_PATHS = {
    "/api/v1/auth/register": "register",
    "/api/v1/auth/login": "login",
}


def authentication_limited_response(error: AuthenticationLimited) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "code": "authentication_rate_limited",
            "message": "Authentication budget exhausted",
        },
        headers={"Retry-After": str(error.retry_after), "Cache-Control": "no-store"},
    )


class RequestProtectionMiddleware:
    """Armazena no máximo 16 KiB e só então entrega o corpo ao framework.

    Conta bytes reais, mesmo sem Content-Length ou com header subdeclarado.
    Rejeições não drenam um corpo arbitrário; desconexão não executa endpoint.
    Lifespan e WebSocket passam sem consumir o canal receive.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        operation = AUTH_PATHS.get(scope["path"].rstrip("/"))
        if scope["method"] == "POST" and operation:
            peer = scope.get("client")
            try:
                scope["app"].state.auth_limits.check_ip(
                    operation, peer[0] if peer else None
                )
            except AuthenticationLimited as error:
                await authentication_limited_response(error)(scope, receive, send)
                return

        async def too_large() -> None:
            await JSONResponse(
                status_code=413,
                content={
                    "code": "request_too_large",
                    "message": "Request body exceeds 16384 bytes",
                },
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)

        for name, value in scope["headers"]:
            if name == b"content-length":
                # Compare without converting an attacker-controlled huge integer.
                length = value.strip().lstrip(b"0") or b"0"
                if length.isdigit() and (
                    len(length) > 5 or (len(length) == 5 and length > b"16384")
                ):
                    await too_large()
                    return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                await too_large()
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
