"""Modelos e utilitários numéricos compartilhados."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field


class ErroColeta(Exception):
    """Falha de coleta/parsing esperada, com mensagem já adequada para o log."""


class ErroEstrutura(ErroColeta):
    """A estrutura da fonte mudou (layout, colunas, base do índice...)."""


@dataclass
class Registro:
    """Um valor publicado para uma competência."""
    competencia: str                 # AAAA-MM
    indice: float
    var_mes: float | None = None     # % publicada pela fonte
    var_12m: float | None = None     # % publicada pela fonte (None se não publicada)


@dataclass
class ResultadoColeta:
    registros: list[Registro]
    fonte: dict = field(default_factory=dict)   # url, arquivo, método usado...
    base_detectada: str | None = None
    avisos: list[str] = field(default_factory=list)


_RE_NUM = re.compile(r"^[+-]?\d+(\.\d+)?$")


def num_br(valor) -> float | None:
    """Converte textos numéricos brasileiros/ISO em float.

    Aceita '1.234,56', '0,61%', '- 0,22%' (espaço após o sinal), sinais tipográficos
    (– −), NBSP, '-' e vazio (=> None). Lança ValueError para texto não numérico.
    """
    if valor is None:
        return None
    if isinstance(valor, bool):
        raise ValueError("booleano não é número")
    if isinstance(valor, (int, float)):
        if isinstance(valor, float) and (math.isnan(valor) or math.isinf(valor)):
            return None
        return float(valor)
    t = str(valor).replace(" ", " ").strip()
    t = t.replace("–", "-").replace("−", "-").replace("—", "-")
    t = t.replace("%", "").replace(" ", "")
    if t in ("", "-", "--", "—", "nd", "ND", "n.d.", "..."):
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    if not _RE_NUM.match(t):
        raise ValueError(f"valor não numérico: {valor!r}")
    return float(t)
