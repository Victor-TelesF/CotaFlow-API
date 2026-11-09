"""Limite operacional compartilhado pelas duas consultas paginadas."""

from typing import Annotated

from fastapi import Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import TypeAdapter, ValidationError

PageOffset = Annotated[
    int,
    Query(ge=0, le=10000, description="Operational offset limit: 10,000 items"),
]


_offset_adapter = TypeAdapter(PageOffset)


def validate_page_offset(request: Request) -> None:
    """Interrompe antes da identidade, inclusive para entrada não numérica.

    A validação declarativa da rota acumula erros e ainda resolve outras
    dependências. Esta guarda levanta o mesmo erro imediatamente, antes do SQL.
    """
    try:
        _offset_adapter.validate_python(request.query_params.get("offset", 0))
    except ValidationError as error:
        details = error.errors(include_input=False, include_context=False)
        for item in details:
            item["loc"] = ("query", "offset", *item["loc"])
        raise RequestValidationError(details) from None
