"""Ponto único de obtenção da data usada na validação das operações."""

from datetime import date


def reference_date() -> date:
    """Retorna a data local do processo, sem buscar relógio na rede.

    Testes substituem esta função para controlar a fronteira de data futura.
    """
    return date.today()
