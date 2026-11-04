"""Armazenamento JSON: um arquivo por índice, gravação atômica, upsert sem duplicidade
e preservação de revisões.

Estrutura de data/<codigo>.json
  schema_version, indice{...}, fonte{...}, metodologia{...},
  atualizado_em (só muda quando os DADOS mudam), primeira_competencia, ultima_competencia,
  total_registros, serie[ {competencia, data_referencia, indice, var_mes, var_12m,
  var_12m_origem, primeira_coleta, revisoes?} ] em ordem cronológica crescente.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime
from pathlib import Path

from .config import SCHEMA_VERSION, IndiceCfg, dir_dados
from .modelos import Registro
from .tempo import data_referencia, iso, ordinal, somar_meses

CAMPOS_FONTE = ("indice", "var_mes", "var_12m")
CHAVES_ENTRADA = ("competencia", "data_referencia", "indice", "var_mes", "var_12m",
                  "var_12m_origem", "primeira_coleta", "revisoes")


def caminho(codigo: str, base: Path | None = None) -> Path:
    return (base or dir_dados()) / f"{codigo}.json"


def carregar(codigo: str, base: Path | None = None) -> dict | None:
    p = caminho(codigo, base)
    if not p.exists():
        return None
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def escrever_json_atomico(path: Path, texto: str) -> None:
    """Grava em arquivo temporário no mesmo diretório e troca por os.replace (atômico)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(texto)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def serializar(doc: dict) -> str:
    """JSON legível: cabeçalho indentado e uma competência por linha (diffs enxutos)."""
    cabeca = {k: v for k, v in doc.items() if k != "serie"}
    cabeca["serie"] = "@@SERIE@@"
    texto = json.dumps(cabeca, ensure_ascii=False, indent=2)
    linhas = ",\n".join("    " + json.dumps(e, ensure_ascii=False, separators=(", ", ": "))
                        for e in doc.get("serie", []))
    corpo = "[\n" + linhas + "\n  ]" if linhas else "[]"
    return texto.replace('"@@SERIE@@"', corpo) + "\n"


def salvar(codigo: str, doc: dict, base: Path | None = None) -> None:
    escrever_json_atomico(caminho(codigo, base), serializar(doc))


def _arred(x):
    return None if x is None else round(float(x), 10)


def iguais(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


def detectar_mudancas(doc_atual: dict | None, registros: list[Registro]) -> dict:
    """Compara a coleta com o armazenado (sem alterar nada)."""
    atual = {e["competencia"]: e for e in (doc_atual or {}).get("serie", [])}
    novos, revisoes = [], []
    for r in registros:
        e = atual.get(r.competencia)
        if e is None:
            novos.append(r.competencia)
            continue
        for campo in CAMPOS_FONTE:
            novo = _arred(getattr(r, campo))
            velho = e.get(campo)
            if campo == "var_12m" and e.get("var_12m_origem") == "calculada":
                continue                    # derivada: recalculada, não é revisão da fonte
            if novo is None:
                continue                    # a fonte não informou: mantém o valor guardado
            if not iguais(novo, velho):
                revisoes.append({"competencia": r.competencia, "campo": campo, "de": velho, "para": novo})
    fonte_comps = {r.competencia for r in registros}
    ausentes = sorted(c for c in atual if c not in fonte_comps)
    return {"novos": sorted(novos), "revisoes": revisoes, "ausentes": ausentes}


def _recalcular_derivados(serie: list[dict]) -> int:
    """Preenche var_12m calculada (razão de números-índice) onde a fonte não publicou."""
    por_comp = {e["competencia"]: e for e in serie}
    alterados = 0
    for e in serie:
        if e.get("var_12m_origem") == "fonte":
            continue
        ant = por_comp.get(somar_meses(e["competencia"], -12))
        novo = None if ant is None else round((e["indice"] / ant["indice"] - 1) * 100, 10)
        origem = "calculada" if novo is not None else None
        if not iguais(novo, e.get("var_12m")) or e.get("var_12m_origem") != origem:
            e["var_12m"], e["var_12m_origem"] = novo, origem
            alterados += 1
    return alterados


def mesclar(doc_atual: dict | None, registros: list[Registro], cfg: IndiceCfg, agora: datetime,
            fonte: dict | None = None) -> tuple[dict, dict]:
    """Upsert por competência. Retorna (novo_doc, resumo). Nunca remove competências."""
    ts = iso(agora)
    doc = json.loads(json.dumps(doc_atual)) if doc_atual else None
    mud = detectar_mudancas(doc, registros)
    serie = doc["serie"] if doc else []
    por_comp = {e["competencia"]: e for e in serie}
    for r in sorted(registros, key=lambda x: x.competencia):
        e = por_comp.get(r.competencia)
        if e is None:
            e = {"competencia": r.competencia, "data_referencia": data_referencia(r.competencia),
                 "indice": _arred(r.indice), "var_mes": _arred(r.var_mes),
                 "var_12m": _arred(r.var_12m),
                 "var_12m_origem": "fonte" if r.var_12m is not None else None,
                 "primeira_coleta": ts}
            serie.append(e)
            por_comp[r.competencia] = e
            continue
        for campo in CAMPOS_FONTE:
            novo = _arred(getattr(r, campo))
            if novo is None:
                continue
            if campo == "var_12m":
                if e.get("var_12m_origem") == "calculada" and not iguais(novo, e.get("var_12m")):
                    e["var_12m_origem"] = "fonte"   # a fonte passou a publicar o valor
                    e["var_12m"] = novo
                    continue
            if not iguais(novo, e.get(campo)):
                e.setdefault("revisoes", []).append({"em": ts, "campo": campo, "de": e.get(campo), "para": novo})
                e[campo] = novo
                if campo == "var_12m":
                    e["var_12m_origem"] = "fonte"
    serie.sort(key=lambda e: e["competencia"])
    derivados = _recalcular_derivados(serie)
    for i, e in enumerate(serie):
        serie[i] = {k: e[k] for k in CHAVES_ENTRADA if k in e}
    alterado = bool(mud["novos"] or mud["revisoes"] or derivados or doc is None)

    novo = {
        "schema_version": SCHEMA_VERSION,
        "indice": cfg.meta_json(),
        "fonte": {
            "nome": cfg.fonte_nome, "url_oficial": cfg.fonte_url, "metodo_coleta": cfg.metodo_coleta,
            **{k: v for k, v in (fonte or {}).items() if k in ("metodo", "url", "arquivo", "post")},
        },
        "metodologia": {"url": cfg.metodologia_url, "acumulado": cfg.metodologia_acumulado,
                        "observacoes": list(cfg.observacoes)},
        "atualizado_em": ts if alterado or not doc else doc.get("atualizado_em", ts),
        "primeira_competencia": serie[0]["competencia"],
        "ultima_competencia": serie[-1]["competencia"],
        "total_registros": len(serie),
        "serie": serie,
    }
    return novo, {**mud, "derivados_recalculados": derivados, "alterado": alterado,
                  "total": len(serie), "ultima_competencia": serie[-1]["competencia"]}
