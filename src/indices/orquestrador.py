"""Orquestra a atualização dos índices.

Garantias:
* cada índice roda isolado (exceção em um não interrompe os demais);
* nada é gravado se a validação falhar — os dados anteriores permanecem intactos;
* trava de execução para impedir gravações concorrentes;
* cada execução gera log individual + consolidado e atualiza data/status.json.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from . import armazenamento as arm
from . import logs as lg
from . import validacao
from .coletores import COLETORES
from .config import FUSO, HORARIOS, INDICES, ORDEM_EXECUCAO, SCHEMA_VERSION, dir_dados
from .modelos import ErroColeta, ResultadoColeta
from .tempo import agora as _agora, iso, rotulo

LOCK_VELHO_S = 30 * 60


class TravaOcupada(RuntimeError):
    pass


@contextmanager
def trava(base: Path):
    """Trava exclusiva por arquivo (O_EXCL). Lock abandonado há mais de 30 min é descartado."""
    base.mkdir(parents=True, exist_ok=True)
    p = base / ".atualizacao.lock"
    for _ in range(2):
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {time.time()}".encode())
            os.close(fd)
            break
        except FileExistsError:
            if time.time() - p.stat().st_mtime > LOCK_VELHO_S:
                p.unlink(missing_ok=True)
                continue
            raise TravaOcupada("Outra atualização está em andamento (trava ativa)")
    else:                                                      # pragma: no cover
        raise TravaOcupada("Não foi possível obter a trava de atualização")
    try:
        yield
    finally:
        p.unlink(missing_ok=True)


def _msg_alteracao(resumo: dict) -> str:
    partes = []
    if resumo["novos"]:
        n = resumo["novos"]
        partes.append(f"{len(n)} nova(s) competência(s): " + ", ".join(rotulo(c) for c in n[-3:]))
    if resumo["revisoes"]:
        partes.append(f"{len({r['competencia'] for r in resumo['revisoes']})} revisão(ões) de valores publicados")
    return "; ".join(partes) if partes else f"Sem novidades (última competência: {rotulo(resumo['ultima_competencia'])})"


def atualizar_indice(codigo: str, *, execucao_id: str, gatilho: str, base: Path, dry_run: bool = False,
                     coletor=None, relogio: Callable[[], datetime] = _agora,
                     eco: Callable[[str], None] | None = print) -> dict:
    """Coleta, valida e (se válido) grava um índice. Nunca lança exceção: devolve a entrada de log."""
    cfg = INDICES[codigo]
    ex = lg.Execucao(codigo, "Atualização (simulação)" if dry_run else "Atualização", gatilho, execucao_id,
                     relogio, eco)
    try:
        ex.log(f"Início da atualização do {cfg.sigla}")
        doc_atual = arm.carregar(codigo, base)
        if doc_atual is None:
            ex.log("Nenhum histórico armazenado ainda: primeira carga completa", nivel="aviso")
        col = coletor or COLETORES[codigo](cfg)
        res: ResultadoColeta = col.coletar(ex.log)

        ex.log("Validando dados coletados")
        v = validacao.validar(res.registros, doc_atual, cfg, res.base_detectada, relogio())
        for a in v.avisos:                     # avisos do coletor já foram registrados por ele
            ex.log(a, nivel="aviso")
        if not v.ok:
            for e in v.erros:
                ex.log(e, nivel="erro")
            ex.log("Dados NÃO gravados: histórico anterior preservado", nivel="erro")
            return ex.finalizar("erro", "Validação reprovou os dados coletados: " + v.erros[0], {
                "gravado": False, "registros_fonte": len(v.registros)})

        novo, resumo = arm.mesclar(doc_atual, v.registros, cfg, relogio(), res.fonte)
        resumo_log = {"gravado": False, "registros_fonte": len(v.registros), "novos": resumo["novos"],
                      "revisoes": resumo["revisoes"][:20], "total": resumo["total"],
                      "ultima_competencia": resumo["ultima_competencia"], "ausentes": resumo["ausentes"][:20]}
        if resumo["alterado"] and not dry_run:
            arm.salvar(codigo, novo, base)
            resumo_log["gravado"] = True
            ex.log("Arquivo JSON atualizado: " + _msg_alteracao(resumo), registros=resumo["total"])
        elif resumo["alterado"]:
            ex.log("Simulação: haveria alteração — " + _msg_alteracao(resumo))
        else:
            ex.log("Dados já atualizados: nenhuma alteração necessária")
        n_avisos = len(res.avisos) + len(v.avisos)
        resultado = "aviso" if n_avisos else "sucesso"
        return ex.finalizar(resultado, _msg_alteracao(resumo) + (f" — {n_avisos} aviso(s)" if n_avisos else ""),
                            resumo_log)
    except ErroColeta as e:
        ex.log(f"Falha na coleta: {e}", nivel="erro")
        ex.log("Dados anteriores preservados", nivel="erro")
        return ex.finalizar("erro", f"Falha na coleta: {e}", {"gravado": False})
    except Exception as e:                                       # noqa: BLE001 - isolamento por índice
        ex.log(f"Erro inesperado ({type(e).__name__}): {e}", nivel="erro")
        ex.log("Dados anteriores preservados", nivel="erro")
        return ex.finalizar("erro", f"Erro inesperado ({type(e).__name__}): {e}", {"gravado": False})


def _ler_status(base: Path) -> dict:
    p = base / "status.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def _info_indice(codigo: str, entrada: dict, anterior: dict, base: Path) -> dict:
    doc = arm.carregar(codigo, base)
    info = dict(anterior or {})
    info.update({
        "resultado": entrada["resultado"], "ultima_tentativa": entrada["fim"],
        "mensagem": entrada["mensagem"], "log_id": entrada["id"],
        "ultima_competencia": doc["ultima_competencia"] if doc else None,
        "ultima_atualizacao_dados": doc["atualizado_em"] if doc else None,
    })
    if entrada["resultado"] in ("sucesso", "aviso"):
        info["ultima_coleta_ok"] = entrada["fim"]
    else:
        info["ultima_falha"] = {"em": entrada["fim"], "mensagem": entrada["mensagem"]}
    return info


def executar(indices: list[str] | None = None, *, gatilho: str = "manual", dry_run: bool = False,
             base: Path | None = None, pular_se_recente_min: int | None = None,
             coletores: dict | None = None, relogio: Callable[[], datetime] = _agora,
             eco: Callable[[str], None] | None = print) -> dict:
    """Executa a rotina completa. Retorna {'resultado', 'entradas', 'pulada'}."""
    base = base or dir_dados()
    codigos = [c for c in ORDEM_EXECUCAO if (not indices or c in indices)]
    inicio = relogio()
    exec_id = inicio.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(2)

    status = _ler_status(base)
    if pular_se_recente_min and status.get("execucao", {}).get("fim"):
        ult = datetime.fromisoformat(status["execucao"]["fim"])
        if inicio - ult < timedelta(minutes=pular_se_recente_min):
            if eco:
                eco(f"Execução de reserva ignorada: última execução concluída em {status['execucao']['fim']}")
            return {"resultado": "ignorada", "entradas": [], "pulada": True}

    try:
        with trava(base):
            entradas = []
            for cod in codigos:
                col = (coletores or {}).get(cod)
                e = atualizar_indice(cod, execucao_id=exec_id, gatilho=gatilho, base=base, dry_run=dry_run,
                                     coletor=col, relogio=relogio, eco=eco)
                entradas.append(e)
                if not dry_run:
                    lg.anexar(e, base)
            fim = relogio()
            geral = ("erro" if all(x["resultado"] == "erro" for x in entradas) else
                     "aviso" if any(x["resultado"] != "sucesso" for x in entradas) else "sucesso")
            if not dry_run:
                novo_status = {
                    "schema_version": SCHEMA_VERSION, "gerado_em": iso(fim), "fuso": FUSO,
                    "agendamento": {"horarios": list(HORARIOS), "fuso": FUSO},
                    "execucao": {"id": exec_id, "inicio": iso(inicio), "fim": iso(fim), "gatilho": gatilho,
                                 "resultado": geral},
                    "indices": {c: _info_indice(c, e, status.get("indices", {}).get(c, {}), base)
                                for c, e in zip(codigos, entradas)},
                }
                for c, i in status.get("indices", {}).items():          # preserva índices não executados
                    novo_status["indices"].setdefault(c, i)
                arm.escrever_json_atomico(base / "status.json",
                                          json.dumps(novo_status, ensure_ascii=False, indent=2) + "\n")
                lg.reconstruir_visoes(list(ORDEM_EXECUCAO), base, fim)
            return {"resultado": geral, "entradas": entradas, "pulada": False}
    except TravaOcupada as e:
        # Registra o conflito no log do sistema; não toca nos dados.
        ex = lg.Execucao("sistema", "Atualização", gatilho, exec_id, relogio, eco)
        ex.log(str(e), nivel="erro")
        entrada = ex.finalizar("erro", f"Execução cancelada: {e}", {"gravado": False})
        if not dry_run:
            lg.anexar(entrada, base)
            lg.reconstruir_visoes(list(ORDEM_EXECUCAO), base, relogio())
        return {"resultado": "erro", "entradas": [entrada], "pulada": False}
