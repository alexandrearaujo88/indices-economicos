"""Utilitários de tempo: competências (AAAA-MM) e fuso America/Maceio."""
from __future__ import annotations

import re
from datetime import datetime

from .config import TZ

MESES_ABREV = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
MESES_NOME = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
              "agosto", "setembro", "outubro", "novembro", "dezembro"]

_RE_COMP = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def agora() -> datetime:
    """Instante atual com fuso America/Maceio (UTC−03:00)."""
    return datetime.now(TZ)


def iso(dt: datetime) -> str:
    return dt.astimezone(TZ).isoformat(timespec="seconds")


def competencia(ano: int, mes: int) -> str:
    if not 1 <= mes <= 12:
        raise ValueError(f"Mês inválido: {mes}")
    return f"{ano:04d}-{mes:02d}"


def parse_competencia(c: str) -> tuple[int, int]:
    m = _RE_COMP.match(c or "")
    if not m:
        raise ValueError(f"Competência inválida: {c!r} (esperado AAAA-MM)")
    return int(m.group(1)), int(m.group(2))


def ordinal(c: str) -> int:
    """Número sequencial de meses, útil para aritmética (diferenças, lacunas)."""
    a, m = parse_competencia(c)
    return a * 12 + (m - 1)


def de_ordinal(n: int) -> str:
    return competencia(n // 12, n % 12 + 1)


def somar_meses(c: str, n: int) -> str:
    return de_ordinal(ordinal(c) + n)


def data_referencia(c: str) -> str:
    """Primeiro dia do mês de competência (data de referência padronizada)."""
    a, m = parse_competencia(c)
    return f"{a:04d}-{m:02d}-01"


def rotulo(c: str) -> str:
    """'2026-09' -> 'set/2026'."""
    a, m = parse_competencia(c)
    return f"{MESES_ABREV[m - 1]}/{a}"


def mes_por_nome(texto: str) -> int | None:
    """Converte nome/abreviação de mês em português (com ou sem acento) para 1..12."""
    t = (texto or "").strip().lower().rstrip(".")
    t = t.replace("ç", "c")
    if t in ("mai", "maio"):
        return 5
    for i, nome in enumerate(MESES_NOME):
        n = nome.replace("ç", "c")
        if t == n or t == n[:3]:
            return i + 1
    return None
