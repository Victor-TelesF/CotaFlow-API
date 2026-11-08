"""Sinal de vida da API, sem verificar PostgreSQL ou provedor de cotações."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """Confirma que a aplicação responde; não é um teste de prontidão do banco."""
    return {"status": "ok"}
