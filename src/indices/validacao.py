"""Validação dos dados coletados ANTES de qualquer gravação.

Erros bloqueiam a atualização (os dados anteriores permanecem intactos); avisos são
registrados e a atualização prossegue.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from .armazenamento import detectar_mudancas
from .config import IndiceCfg
from .modelos import Registro
from .tempo import competencia, ordinal, rotulo, somar_meses

TOL_AVISO = 0.011        # p.p.: arredondamento da fonte (2 casas) explica até ~0,01
TOL_ERRO = 0.10          # p.p.: acima disso a série está inconsistente
REVISAO_GRANDE = 0.005   # 0,5% de variação relativa num índice já publicado
MAX_NOVOS_SEM_AVISO = 3


@dataclass
class Validacao:
    registros: list[Registro]
    erros: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.erros


def _norm_base(b: str | None) -> str | None:
    return None if b is None else b.replace(" ", "").lower()


def deduplicar(regs: list[Registro], erros: list[str], avisos: list[str]) -> list[Registro]:
    vistos: dict[str, Registro] = {}
    repetidos = 0
    for r in regs:
        ant = vistos.get(r.competencia)
        if ant is None:
            vistos[r.competencia] = r
            continue
        repetidos += 1
        if not (math.isclose(ant.indice, r.indice, rel_tol=1e-9)
                and (ant.var_mes is None or r.var_mes is None or math.isclose(ant.var_mes, r.var_mes, abs_tol=1e-9))):
            erros.append(f"Competência {rotulo(r.competencia)} aparece duas vezes na fonte com valores "
                         f"diferentes ({ant.indice} e {r.indice})")
    if repetidos:
        avisos.append(f"{repetidos} linha(s) duplicada(s) idêntica(s) na fonte foram descartadas")
    return sorted(vistos.values(), key=lambda r: r.competencia)


def validar(registros: list[Registro], doc_atual: dict | None, cfg: IndiceCfg,
            base_detectada: str | None, agora: datetime) -> Validacao:
    erros: list[str] = []
    avisos: list[str] = []
    regs = deduplicar(list(registros), erros, avisos)

    if len(regs) < cfg.min_registros:
        erros.append(f"Apenas {len(regs)} competências na fonte (mínimo esperado: {cfg.min_registros}); "
                     "possível mudança de estrutura ou página incompleta")
        return Validacao(regs, erros, avisos)

    # base do número-índice (rebase quebraria a continuidade da série)
    if base_detectada and cfg.base and "=" in cfg.base and _norm_base(base_detectada) != _norm_base(cfg.base):
        erros.append(f"Base do índice mudou: fonte informa '{base_detectada}', esperado '{cfg.base}'. "
                     "Atualização bloqueada para não misturar bases diferentes")

    # lacunas
    for a, b in zip(regs, regs[1:]):
        if ordinal(b.competencia) - ordinal(a.competencia) != 1:
            erros.append(f"Lacuna na série entre {rotulo(a.competencia)} e {rotulo(b.competencia)}")
            break

    # valores plausíveis
    ruins = []
    for r in regs:
        if not (math.isfinite(r.indice) and r.indice > 0):
            ruins.append(f"{rotulo(r.competencia)}: índice inválido ({r.indice})")
        elif r.var_mes is not None and abs(r.var_mes) > cfg.limite_var_mes:
            ruins.append(f"{rotulo(r.competencia)}: variação mensal implausível ({r.var_mes}%)")
    if ruins:
        erros.append("Valores fora do intervalo plausível: " + "; ".join(ruins[:5]))

    # consistência variação mensal × números-índice
    ordem = {r.competencia: r for r in regs}
    ant_idx = cfg.base_anterior["indice"] if cfg.base_anterior else None
    diverg = []
    for i, r in enumerate(regs):
        prev = regs[i - 1].indice if i > 0 else (
            ant_idx if cfg.base_anterior and cfg.base_anterior["competencia"] == somar_meses(r.competencia, -1) else None)
        if prev is None or r.var_mes is None:
            continue
        d = abs(r.var_mes - (r.indice / prev - 1) * 100)
        if d > TOL_ERRO:
            erros.append(f"Variação mensal de {rotulo(r.competencia)} ({r.var_mes}%) incompatível com os "
                         f"números-índice ({(r.indice / prev - 1) * 100:.4f}%)")
            break
        if d > TOL_AVISO:
            diverg.append(f"{rotulo(r.competencia)} (Δ {d:.3f} p.p.)")
    if diverg:
        avisos.append(f"{len(diverg)} competência(s) com variação mensal levemente divergente da razão "
                      f"entre índices: {', '.join(diverg[:4])}")

    # competência no futuro
    if regs[-1].competencia > competencia(agora.year, agora.month):
        erros.append(f"Competência futura na fonte: {rotulo(regs[-1].competencia)}")

    # comparação com o histórico armazenado
    if doc_atual and doc_atual.get("serie"):
        n_atual = len(doc_atual["serie"])
        if regs[-1].competencia < doc_atual["ultima_competencia"]:
            erros.append(f"A fonte regrediu: última competência {rotulo(regs[-1].competencia)} é anterior à "
                         f"já armazenada ({rotulo(doc_atual['ultima_competencia'])})")
        if len(regs) < 0.9 * n_atual:
            erros.append(f"Fonte com {len(regs)} competências contra {n_atual} armazenadas "
                         "(perda superior a 10%)")
        mud = detectar_mudancas(doc_atual, regs)
        if mud["ausentes"]:
            avisos.append(f"{len(mud['ausentes'])} competência(s) armazenada(s) não constam mais na fonte "
                          f"(mantidas): {', '.join(rotulo(c) for c in mud['ausentes'][:4])}")
        revs = mud["revisoes"]
        if revs:
            atuais = {e["competencia"]: e for e in doc_atual["serie"]}
            grandes = [x for x in revs if x["campo"] == "indice" and x["de"]
                       and abs(x["para"] / x["de"] - 1) > REVISAO_GRANDE]
            comps = {x["competencia"] for x in revs}
            if grandes:
                x = grandes[0]
                erros.append(f"Revisão grande detectada em {rotulo(x['competencia'])}: índice "
                             f"{x['de']} → {x['para']} (>{REVISAO_GRANDE:.1%}); possível mudança de base ou "
                             "erro de leitura")
            elif len(comps) > max(3, int(0.10 * n_atual)):
                erros.append(f"{len(comps)} competências já publicadas mudaram de valor de uma vez; "
                             "atualização bloqueada para verificação manual")
            else:
                det = "; ".join(f"{rotulo(x['competencia'])} {x['campo']} {x['de']} → {x['para']}" for x in revs[:3])
                avisos.append(f"Revisão de valores já publicados em {len(comps)} competência(s): {det}")
        if len(mud["novos"]) > MAX_NOVOS_SEM_AVISO:
            avisos.append(f"{len(mud['novos'])} competências novas de uma vez (esperado até "
                          f"{MAX_NOVOS_SEM_AVISO}); confira se houve período sem coleta")
    return Validacao(regs, erros, avisos)
