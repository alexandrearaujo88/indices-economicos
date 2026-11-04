"""Alertas por e-mail via GitHub Issues (sem servidor e sem serviços externos).

Cada problema vira UMA issue aberta (rótulo `alerta-indices`), identificada por um marcador
`<!-- alerta:<chave> -->` no corpo. O dono do repositório é mencionado (`@login`), o que gera o
e-mail pelo mecanismo nativo do GitHub. Regras anti-repetição:

* problema novo            -> abre a issue (1 e-mail);
* problema que continua    -> só edita o corpo da issue (edição não notifica);
* problema que desapareceu -> comenta e fecha a issue (1 e-mail).

Chaves: `coletor-ipca`, `coletor-icti`, `coletor-ist` (falha de coleta/validação), `workflow` (erro
estrutural do fluxo ou da publicação) e `atraso` (execução agendada que não ocorreu).
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from .config import INDICES, ORDEM_EXECUCAO, TZ
from .logs import sanitizar

ROTULO = "alerta-indices"
SLOTS_UTC = (12, 22)              # 09h e 19h em Maceió
TOLERANCIA_MIN = 135              # 45 min da reserva + 90 min de margem para atrasos do GitHub
COBERTURA_MIN = 120               # mesma janela usada para a reserva ignorar execuções recentes
RE_MARCADOR = re.compile(r"<!--\s*alerta:([a-z0-9-]+)\s*-->")

Gh = Callable[[list[str]], str]


@dataclass(frozen=True)
class Contexto:
    """Dados do ambiente do GitHub Actions usados para montar links e mencionar o dono."""
    repo: str = "usuario/repositorio"
    dono: str = "usuario"
    servidor: str = "https://github.com"
    run_id: str = ""

    @property
    def url_run(self) -> str:
        return f"{self.servidor}/{self.repo}/actions/runs/{self.run_id}" if self.run_id else \
            f"{self.servidor}/{self.repo}/actions"

    @property
    def url_workflow(self) -> str:
        return f"{self.servidor}/{self.repo}/actions/workflows/atualizar.yml"

    @property
    def url_site(self) -> str:
        nome = self.repo.split("/")[-1]
        return f"https://{self.dono.lower()}.github.io/{nome}/"


def contexto_do_ambiente(env=os.environ) -> Contexto:
    repo = env.get("GITHUB_REPOSITORY", "usuario/repositorio")
    return Contexto(repo=repo, dono=env.get("GITHUB_REPOSITORY_OWNER", repo.split("/")[0]),
                    servidor=env.get("GITHUB_SERVER_URL", "https://github.com"), run_id=env.get("GITHUB_RUN_ID", ""))


@dataclass(frozen=True)
class Incidente:
    chave: str
    titulo: str
    corpo: str          # sem o marcador; ele é acrescentado ao publicar


def _local(dt: datetime | str | None) -> str:
    if dt is None:
        return "—"
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    return dt.astimezone(TZ).strftime("%d/%m/%Y %H:%M") + " (Maceió)"


def _tabela(linhas: list[tuple[str, str]]) -> str:
    return "| | |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in linhas)


def _cabeca(ctx: Contexto) -> str:
    return f"@{ctx.dono}\n\n"


# --------------------------------------------------------------------------- avaliação pós-execução
def avaliar_execucao(status: dict | None, *, resultado_atualizar: str, resultado_publicar: str,
                     inicio_run: datetime, ctx: Contexto, coleta: str = "",
                     gravacao: str = "") -> tuple[dict[str, Incidente], set[str]]:
    """Retorna (incidentes ativos, chaves avaliadas). Só as chaves avaliadas podem ser fechadas:
    uma execução ignorada (reserva) ou uma simulação não altera o status e não pode 'curar' alertas."""
    ativos: dict[str, Incidente] = {}
    avaliadas: set[str] = set()
    corte = inicio_run - timedelta(seconds=60)

    for cod in ORDEM_EXECUCAO:
        info = ((status or {}).get("indices") or {}).get(cod) or {}
        tentativa = info.get("ultima_tentativa")
        if not tentativa or datetime.fromisoformat(tentativa) < corte:
            continue                                   # este índice não rodou nesta execução
        chave = f"coletor-{cod}"
        avaliadas.add(chave)
        if info.get("resultado") != "erro":
            continue
        sigla = INDICES[cod].sigla
        ok = (info.get("ultima_coleta_ok"))
        ativos[chave] = Incidente(chave, f"[Alerta] Falha na coleta do {sigla}", _cabeca(ctx) + (
            f"**Falha na coleta do {sigla}.** Os dados anteriores foram preservados.\n\n" + _tabela([
                ("Índice", sigla),
                ("Horário da falha", _local(tentativa)),
                ("Motivo", sanitizar(info.get("mensagem") or "não informado", 300)),
                ("Última coleta bem-sucedida", _local(ok) if ok else "—"),
                ("Log da execução", f"[execução no GitHub Actions]({ctx.url_run})"),
                ("Registros no site", f"[{sigla}]({ctx.url_site}#/logs/{cod})"),
            ]) + "\n\nEste alerta é atualizado se a falha continuar e fechado automaticamente quando a coleta "
                 "voltar a funcionar."))

    # O fluxo fica vermelho quando um coletor falha (coleta == "1") e isso já é coberto pelo alerta do coletor.
    # Só é falha ESTRUTURAL se o job falhou por outro motivo (instalação, gravação no repositório etc.).
    so_coletor = coleta == "1" and gravacao == "success"
    falha_fluxo = resultado_atualizar == "failure" and not so_coletor
    falha_pub = resultado_publicar == "failure"
    if falha_fluxo or falha_pub or (resultado_atualizar in ("success", "failure") and resultado_publicar == "success"):
        avaliadas.add("workflow")
    if falha_fluxo or falha_pub:
        etapa = " e ".join(n for n, f in (("coleta/gravação", falha_fluxo), ("publicação do site", falha_pub)) if f)
        ativos["workflow"] = Incidente("workflow", '[Alerta] Erro no workflow "Atualizar índices"', _cabeca(ctx) + (
            f"**O fluxo falhou na etapa de {etapa}.** Não é uma falha de coleta de índice específico.\n\n" + _tabela([
                ("Horário", _local(datetime.now(timezone.utc))),
                ("Atualizar (coleta e gravação)", "falhou" if falha_fluxo else "ok"),
                ("Publicar (site)", "falhou" if falha_pub else (
                    "não executado" if resultado_publicar in ("skipped", "") else "ok")),
                ("Log da execução", f"[execução no GitHub Actions]({ctx.url_run})"),
            ]) + "\n\nA falha de um índice isolado não gera este alerta: ela aparece como \"Falha na coleta\". "
                 "O alerta é fechado automaticamente quando uma execução terminar sem erros."))
    return ativos, avaliadas


# --------------------------------------------------------------------------- vigia de execuções agendadas
def ultimo_slot(agora: datetime) -> datetime:
    agora = agora.astimezone(timezone.utc)
    cand = [datetime(d.year, d.month, d.day, h, tzinfo=timezone.utc)
            for d in (agora - timedelta(days=1), agora) for h in SLOTS_UTC]
    return max(c for c in cand if c <= agora)


def avaliar_atraso(status: dict | None, agora: datetime, ctx: Contexto) -> tuple[dict[str, Incidente], set[str]]:
    slot = ultimo_slot(agora)
    if agora.astimezone(timezone.utc) < slot + timedelta(minutes=TOLERANCIA_MIN):
        return {}, set()                                # cedo demais para julgar este horário
    fim = None
    try:
        if status and status.get("execucao", {}).get("fim"):
            fim = datetime.fromisoformat(status["execucao"]["fim"])
    except ValueError:
        fim = None
    if fim is not None and fim >= slot - timedelta(minutes=COBERTURA_MIN):
        return {}, {"atraso"}
    return {"atraso": Incidente("atraso", "[Alerta] Atualização automática não ocorreu", _cabeca(ctx) + (
        f"**Nenhuma execução foi concluída após o horário agendado de {_local(slot)}.**\n\n" + _tabela([
            ("Horário esperado", _local(slot)),
            ("Tolerância", f"{TOLERANCIA_MIN} min (inclui a execução de reserva)"),
            ("Última execução concluída", _local(fim) if fim else "não encontrada"),
            ("Fluxo", f"[Atualizar índices]({ctx.url_workflow})"),
        ]) + "\n\nCausas comuns: atraso ou descarte de agendamentos pelo GitHub, fluxo desativado (o GitHub desativa "
             "agendamentos de repositórios públicos após 60 dias sem atividade) ou falha antes da gravação. "
             "Para normalizar, abra o fluxo e use **Run workflow**. O alerta fecha sozinho quando uma execução ocorrer."))}, {"atraso"}


# --------------------------------------------------------------------------- teste do canal de alertas
def avaliar_teste(abrir: bool, ctx: Contexto) -> tuple[dict[str, Incidente], set[str]]:
    """Abre (ou encerra) um alerta fictício para confirmar que o e-mail chega, sem depender de uma falha real."""
    if not abrir:
        return {}, {"teste"}
    return {"teste": Incidente("teste", "[Alerta] Teste do canal de notificações", _cabeca(ctx) + (
        "**Este é um teste.** Se você recebeu este e-mail, os alertas dos índices estão chegando.\n\n"
        f"Abertura: {_local(datetime.now(timezone.utc))}. Para encerrar, execute o fluxo "
        f"[Vigiar atualizações]({ctx.servidor}/{ctx.repo}/actions/workflows/vigiar.yml) com a opção "
        "\"encerrar\" (ou feche esta issue)."))}, {"teste"}


# --------------------------------------------------------------------------- sincronização com as issues
class ErroGh(RuntimeError):
    pass


def gh_real(args: list[str]) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise ErroGh(f"`gh {' '.join(args[:2])}` falhou: {sanitizar(r.stderr or r.stdout, 300)}")
    return r.stdout


def _corpo_final(inc: Incidente) -> str:
    return f"{inc.corpo}\n\n<!-- alerta:{inc.chave} -->"


def listar_abertas(gh: Gh) -> dict[str, list[dict]]:
    import json
    dados = json.loads(gh(["issue", "list", "--label", ROTULO, "--state", "open", "--limit", "100",
                           "--json", "number,body"]) or "[]")
    out: dict[str, list[dict]] = {}
    for it in dados:
        m = RE_MARCADOR.search(it.get("body") or "")
        if m:
            out.setdefault(m.group(1), []).append(it)
    for lst in out.values():
        lst.sort(key=lambda i: i["number"])
    return out


def sincronizar(ativos: dict[str, Incidente], avaliadas: set[str], gh: Gh,
                *, simular: bool = False) -> list[tuple[str, str]]:
    """Aplica as regras anti-repetição. Retorna as ações executadas: [('abrir'|'atualizar'|'fechar', chave)]."""
    if not ativos and not avaliadas:
        return []
    acoes: list[tuple[str, str]] = []
    abertas = listar_abertas(gh)
    if ativos and not simular:
        gh(["label", "create", ROTULO, "--force", "--color", "B60205",
            "--description", "Alertas automáticos do monitoramento dos índices"])
    for chave, inc in sorted(ativos.items()):
        existentes = abertas.get(chave, [])
        corpo = _corpo_final(inc)
        if not existentes:
            acoes.append(("abrir", chave))
            if not simular:
                gh(["issue", "create", "--title", inc.titulo, "--body", corpo, "--label", ROTULO])
            continue
        principal = existentes[0]
        if (principal.get("body") or "").strip() != corpo.strip():
            acoes.append(("atualizar", chave))
            if not simular:
                gh(["issue", "edit", str(principal["number"]), "--body", corpo])      # edição não notifica
        for extra in existentes[1:]:                                                  # duplicadas por corrida
            acoes.append(("fechar", chave))
            if not simular:
                gh(["issue", "close", str(extra["number"]), "--reason", "not planned",
                    "--comment", "Duplicada de um alerta já aberto."])
    for chave in sorted(avaliadas - set(ativos)):
        for it in abertas.get(chave, []):
            acoes.append(("fechar", chave))
            if not simular:
                gh(["issue", "close", str(it["number"]), "--reason", "completed",
                    "--comment", "Problema resolvido: a verificação mais recente terminou sem este erro. "
                                 "Alerta encerrado automaticamente."])
    return acoes
