"""Logs de execução: registro estruturado (JSONL por índice e por ano) + visões
recentes em JSON para a interface web (individuais e consolidada, mais novos primeiro).

Nada sensível é gravado: mensagens passam por `sanitizar` (sem caminhos locais, tokens,
IPs ou stack traces). O projeto não usa credenciais.
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Callable

from .armazenamento import escrever_json_atomico
from .config import dir_dados
from .tempo import agora as _agora, iso

LIMITE_INDIVIDUAL = 300
LIMITE_CONSOLIDADO = 500
RESULTADOS = ("sucesso", "aviso", "erro")

_PADROES = [
    (re.compile(r"(?i)\b(bearer|token|api[_-]?key|secret|password|senha|authorization)\b\s*[=:]?\s*\S+"), r"\1=***"),
    (re.compile(r"(?:[A-Za-z]:\\|/(?:home|tmp|usr|opt|var|root|runner|mnt|Users)/)[^\s'\"),;]*"), "<caminho>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<ip>"),
    (re.compile(r"\?[^\s'\"]*"), "?…"),
]


def sanitizar(texto, limite: int = 400) -> str:
    t = str(texto)
    t = re.sub(r"Traceback \(most recent call last\):.*", "<stack trace omitido>", t, flags=re.S)
    for rx, sub in _PADROES:
        t = rx.sub(sub, t)
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= limite else t[: limite - 1] + "…"


def _limpar_dados(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        out[k] = sanitizar(v, 300) if isinstance(v, str) else v
    return out


class Execucao:
    """Contexto de uma execução (um índice ou o sistema) que acumula as etapas."""

    def __init__(self, indice: str, operacao: str, gatilho: str, execucao_id: str,
                 relogio: Callable[[], datetime] = _agora, eco: Callable[[str], None] | None = print):
        self.indice, self.operacao, self.gatilho = indice, operacao, gatilho
        self.execucao_id = execucao_id
        self._relogio = relogio
        self._eco = eco
        self.inicio = relogio()
        self.etapas: list[dict] = []
        self.id = f"{self.inicio.strftime('%Y%m%dT%H%M%S')}-{indice}-{secrets.token_hex(2)}"

    def log(self, msg: str, nivel: str = "info", **dados) -> None:
        if nivel not in ("info", "aviso", "erro"):
            nivel = "info"
        e = {"em": iso(self._relogio()), "nivel": nivel, "msg": sanitizar(msg)}
        if dados:
            e["dados"] = _limpar_dados(dados)
        self.etapas.append(e)
        if self._eco:
            self._eco(f"[{self.indice}] {nivel.upper():5} {e['msg']}")

    def finalizar(self, resultado: str, mensagem: str, resumo: dict | None = None) -> dict:
        assert resultado in RESULTADOS, resultado
        fim = self._relogio()
        return {
            "id": self.id, "execucao_id": self.execucao_id, "indice": self.indice,
            "operacao": self.operacao, "gatilho": self.gatilho,
            "inicio": iso(self.inicio), "fim": iso(fim),
            "duracao_ms": int((fim - self.inicio).total_seconds() * 1000),
            "resultado": resultado, "mensagem": sanitizar(mensagem),
            "resumo": resumo or {}, "etapas": self.etapas,
        }


# ------------------------------------------------------------------ persistência
def _dir_logs(base: Path | None) -> Path:
    return (base or dir_dados()) / "logs"


def anexar(entrada: dict, base: Path | None = None) -> None:
    """Acrescenta a execução ao arquivo anual do índice (append-only)."""
    ano = entrada["inicio"][:4]
    p = _dir_logs(base) / entrada["indice"] / f"{ano}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entrada, ensure_ascii=False, separators=(",", ":")) + "\n")


def _chave(e: dict):
    return (e["inicio"], e["id"])


def ler_arquivo(codigo: str, base: Path | None = None, limite: int | None = None) -> list[dict]:
    """Lê os JSONL do índice, do ano mais novo para o mais antigo, devolvendo mais novos primeiro."""
    d = _dir_logs(base) / codigo
    if not d.exists():
        return []
    out: list[dict] = []
    for arq in sorted(d.glob("*.jsonl"), reverse=True):
        linhas = []
        for l in arq.read_text(encoding="utf-8").splitlines():
            l = l.strip()
            if not l:
                continue
            try:
                linhas.append(json.loads(l))
            except json.JSONDecodeError:
                continue            # linha truncada por queda no meio da gravação: ignora
        out += linhas
        if limite and len(out) >= limite:
            break
    out.sort(key=_chave, reverse=True)
    return out[:limite] if limite else out


def resumo_consolidado(e: dict) -> dict:
    return {k: e[k] for k in ("id", "execucao_id", "indice", "operacao", "gatilho", "inicio", "fim",
                              "duracao_ms", "resultado", "mensagem") if k in e} | {
        "n_avisos": sum(1 for x in e.get("etapas", []) if x["nivel"] == "aviso"),
        "n_erros": sum(1 for x in e.get("etapas", []) if x["nivel"] == "erro"),
    }


def reconstruir_visoes(codigos: list[str], base: Path | None = None, agora: datetime | None = None) -> None:
    """Gera data/logs/<codigo>.json (detalhado) e data/logs/consolidado.json (resumido)."""
    ts = iso(agora or _agora())
    todos: list[dict] = []
    for cod in list(codigos) + ["sistema"]:
        regs = ler_arquivo(cod, base, LIMITE_INDIVIDUAL)
        if cod == "sistema" and not regs:
            continue
        escrever_json_atomico(_dir_logs(base) / f"{cod}.json", json.dumps(
            {"indice": cod, "gerado_em": ts, "total": len(regs), "registros": regs},
            ensure_ascii=False, separators=(",", ":")) + "\n")
        todos += regs
    todos.sort(key=_chave, reverse=True)
    cons = [resumo_consolidado(e) for e in todos[:LIMITE_CONSOLIDADO]]
    escrever_json_atomico(_dir_logs(base) / "consolidado.json", json.dumps(
        {"gerado_em": ts, "total": len(cons), "registros": cons}, ensure_ascii=False,
        separators=(",", ":")) + "\n")
