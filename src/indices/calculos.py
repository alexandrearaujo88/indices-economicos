"""Cálculo de índices acumulados (implementação de referência, em Decimal).

Metodologia (igual para os três índices, pois todos são NÚMEROS-ÍNDICE mensais encadeados):

    fator = I(mês final) ÷ I(mês imediatamente anterior ao inicial)          [modo "inclusivo"]
    fator = I(mês final) ÷ I(mês inicial)                                    [modo "data_base"]
    acumulado (%) = (fator − 1) × 100

* "inclusivo" (padrão; é a semântica da aplicação original): acumula a variação de TODOS os meses de
  `inicio` a `fim`, inclusive. Ex.: 01/2024 a 12/2024 = variação do ano de 2024.
* "data_base": variação entre dois números-índice (reajuste de data-base a data-base). Ex.: 01/2024 a
  01/2025 = variação em 12 meses encerrada em 01/2025. Não inclui a variação do mês inicial.

Diferença para a aplicação original: o original compunha as variações mensais JÁ ARREDONDADAS em
2 casas; aqui usa-se a razão dos números-índice publicados, que não acumula erro de arredondamento.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, getcontext

from .tempo import ordinal, rotulo, somar_meses

getcontext().prec = 40
MODOS = ("inclusivo", "data_base")
LIMITE_MOEDA_IPCA = "1994-07"       # antes do Plano Real (jul/1994) a moeda era outra


class ErroCalculo(ValueError):
    def __init__(self, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.codigo = codigo
        self.mensagem = mensagem


@dataclass
class Resultado:
    indice: str
    modo: str
    inicio: str
    fim: str
    meses: int
    fator: Decimal
    percentual: Decimal
    numerador: dict
    denominador: dict
    valor_original: Decimal | None = None
    valor_corrigido: Decimal | None = None
    avisos: list[str] = field(default_factory=list)
    metodologia: str = ""

    def como_dict(self) -> dict:
        q = lambda d, n: None if d is None else str(d.quantize(Decimal(1).scaleb(-n), ROUND_HALF_UP))
        return {"indice": self.indice, "modo": self.modo, "inicio": self.inicio, "fim": self.fim,
                "meses": self.meses, "fator": q(self.fator, 8), "percentual": q(self.percentual, 2),
                "valor_original": q(self.valor_original, 2), "valor_corrigido": q(self.valor_corrigido, 2),
                "avisos": self.avisos}


def parse_mm_aaaa(texto: str) -> str:
    """'03/2024' -> '2024-03'. Também aceita '2024-03'."""
    t = (texto or "").strip()
    m = re.fullmatch(r"(0?[1-9]|1[0-2])/(\d{4})", t)
    if m:
        return f"{int(m.group(2)):04d}-{int(m.group(1)):02d}"
    if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", t):
        return t
    raise ErroCalculo("data_invalida", f"Data inválida: {texto!r}. Use o formato MM/AAAA (mês de 01 a 12).")


def parse_valor_br(texto: str) -> Decimal:
    """'1.234,56' -> Decimal('1234.56'). Também aceita '1234.56' e '1234,5'."""
    t = (texto or "").strip().replace("R$", "").replace(" ", "")
    if not t:
        raise ErroCalculo("valor_invalido", "Valor vazio.")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d+)?", t):
        raise ErroCalculo("valor_invalido", f"Valor inválido: {texto!r}. Exemplo: 1.000,00")
    return Decimal(t)


def _indice_em(serie_por_comp: dict, doc: dict, comp: str) -> tuple[Decimal, dict]:
    e = serie_por_comp.get(comp)
    if e is not None:
        return Decimal(repr(e["indice"])), {"competencia": comp, "indice": e["indice"], "origem": "serie"}
    base = doc["indice"].get("base_anterior")
    if base and base["competencia"] == comp and base.get("indice") is not None:
        return Decimal(repr(base["indice"])), {"competencia": comp, "indice": base["indice"], "origem": "base"}
    primeiro = doc["serie"][0]
    if comp == somar_meses(primeiro["competencia"], -1) and primeiro.get("var_mes") is not None:
        ind = Decimal(repr(primeiro["indice"])) / (Decimal(1) + Decimal(repr(primeiro["var_mes"])) / 100)
        return ind, {"competencia": comp, "indice": float(ind), "origem": "reconstruido"}
    raise ErroCalculo("fora_da_serie", f"Não há número-índice para {rotulo(comp)}.")


def acumulado(doc: dict, inicio: str, fim: str, modo: str = "inclusivo", valor: str | Decimal | None = None) -> Resultado:
    """Calcula o acumulado de `doc` (conteúdo de data/<indice>.json) entre `inicio` e `fim` (AAAA-MM)."""
    if modo not in MODOS:
        raise ErroCalculo("modo_invalido", f"Modo de cálculo inválido: {modo!r}")
    sigla = doc["indice"]["sigla"]
    serie = {e["competencia"]: e for e in doc["serie"]}
    primeira, ultima = doc["primeira_competencia"], doc["ultima_competencia"]
    if ordinal(inicio) > ordinal(fim):
        raise ErroCalculo("periodo_invertido", "A data inicial é posterior à data final.")
    if modo == "data_base" and inicio == fim:
        raise ErroCalculo("periodo_vazio", "No modo data-base, as duas datas precisam ser diferentes.")
    for c in (inicio, fim):
        if c > ultima:
            raise ErroCalculo("nao_divulgado", f"{sigla} de {rotulo(c)} ainda não foi divulgado. "
                              f"Último mês disponível: {rotulo(ultima)}.")
    if inicio < primeira:
        raise ErroCalculo("fora_da_serie", f"{sigla} só está disponível a partir de {rotulo(primeira)}.")

    denom_comp = somar_meses(inicio, -1) if modo == "inclusivo" else inicio
    num, num_info = _indice_em(serie, doc, fim)
    den, den_info = _indice_em(serie, doc, denom_comp)
    fator = num / den
    perc = (fator - 1) * 100
    meses = ordinal(fim) - ordinal(denom_comp)

    avisos: list[str] = []
    if den_info["origem"] == "reconstruido":
        avisos.append(f"O índice de {rotulo(denom_comp)} não é publicado; foi reconstruído a partir da variação "
                      "mensal de " + rotulo(inicio) + " (precisão limitada a ±0,005 p.p.).")
    valor_dec = corrigido = None
    if valor not in (None, ""):
        valor_dec = valor if isinstance(valor, Decimal) else parse_valor_br(valor)
        if doc["indice"]["codigo"] == "ipca" and inicio < LIMITE_MOEDA_IPCA:
            avisos.append(
                "Valor corrigido não calculado: para períodos que começam antes de jul/1994 a moeda era outra "
                "(cruzeiro real/URV). O percentual acumulado é válido; converta o valor para Real pela regra "
                "oficial antes de corrigi-lo.")
            valor_dec = None
        else:
            corrigido = valor_dec * fator
    return Resultado(sigla, modo, inicio, fim, meses, fator, perc, num_info, den_info,
                     valor_dec, corrigido, avisos, doc["metodologia"]["acumulado"])
