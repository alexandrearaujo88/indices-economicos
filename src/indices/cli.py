"""Interface de linha de comando.

  python -m indices atualizar [--indice ipca --indice ist] [--dry-run] [--gatilho agendado]
                              [--pular-se-recente 120]
  python -m indices semear --legado <pasta com *_bruto.xlsx>
  python -m indices reconstruir-logs
  python -m indices alertas execucao --atualizar <resultado> --publicar <resultado> --inicio <ISO-UTC>
  python -m indices alertas vigiar
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import alertas as al
from . import logs as lg
from .config import INDICES, ORDEM_EXECUCAO, dir_dados
from .migracao import coletores_legado
from .orquestrador import executar


def _resumo_actions(res: dict, args) -> None:
    """Registra o resultado no resumo da execução no GitHub Actions (inclusive execuções ignoradas)."""
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if not destino:
        return
    linhas = [f"### Atualização dos índices ({args.gatilho})", ""]
    if res.get("pulada"):
        linhas.append("Execução **ignorada**: outra execução recente já cobriu este horário sem falhas.")
    else:
        linhas += ["| Índice | Resultado | Mensagem |", "|---|---|---|"]
        linhas += [f"| {e['indice']} | {e['resultado']} | {e['mensagem']} |" for e in res["entradas"]]
        if any(e["resultado"] == "erro" for e in res["entradas"]):
            linhas += ["", "Há falha de coleta: o alerta é aberto pela etapa de monitoramento."]
    with open(destino, "a", encoding="utf-8") as f:
        f.write("\n".join(linhas) + "\n")


def _alertas(args) -> int:
    ctx = al.contexto_do_ambiente()
    base = dir_dados()
    try:
        status = json.loads((base / "status.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        status = None
    if args.alerta_cmd == "teste":
        ativos, avaliadas = al.avaliar_teste(args.modo == "abrir", ctx)
    elif args.alerta_cmd == "execucao":
        inicio = datetime.fromisoformat(args.inicio.replace("Z", "+00:00"))
        ativos, avaliadas = al.avaliar_execucao(status, resultado_atualizar=args.atualizar,
                                                resultado_publicar=args.publicar, inicio_run=inicio, ctx=ctx,
                                                coleta=args.coleta, gravacao=args.gravacao)
    else:
        agora = datetime.fromisoformat(args.agora.replace("Z", "+00:00")) if args.agora else datetime.now(timezone.utc)
        ativos, avaliadas = al.avaliar_atraso(status, agora, ctx)
    acoes = al.sincronizar(ativos, avaliadas, al.gh_real, simular=args.simular)
    print(f"Alertas: {len(ativos)} ativo(s), {len(avaliadas)} verificação(ões); ações: {acoes or 'nenhuma'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="indices", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("atualizar", help="coleta, valida e grava os índices")
    a.add_argument("--indice", action="append", choices=sorted(INDICES), help="restringe a um índice (repetível)")
    a.add_argument("--dry-run", action="store_true", help="coleta e valida, mas não grava nada")
    a.add_argument("--gatilho", default="manual", choices=["manual", "agendado", "reserva", "local"])
    a.add_argument("--pular-se-recente", type=int, metavar="MIN",
                   help="não executa se a última execução terminou há menos de MIN minutos")
    s = sub.add_parser("semear", help="carga inicial a partir dos arquivos *_bruto.xlsx do projeto original")
    s.add_argument("--legado", required=True, type=Path)
    sub.add_parser("reconstruir-logs", help="regera as visões JSON dos logs a partir dos arquivos anuais")
    al_p = sub.add_parser("alertas", help="abre/atualiza/fecha issues de alerta (usa a CLI `gh`)")
    al_sub = al_p.add_subparsers(dest="alerta_cmd", required=True)
    ex = al_sub.add_parser("execucao", help="avalia o resultado do fluxo de atualização")
    ex.add_argument("--atualizar", required=True, help="resultado do job de coleta (success/failure/...)")
    ex.add_argument("--publicar", default="", help="resultado do job de publicação")
    ex.add_argument("--coleta", default="", help="código de saída da coleta (0 = todos os índices ok)")
    ex.add_argument("--gravacao", default="", help="resultado do passo que grava no repositório")
    ex.add_argument("--inicio", required=True, help="início da execução do fluxo (ISO 8601, UTC)")
    vg = al_sub.add_parser("vigiar", help="verifica se a execução agendada mais recente ocorreu")
    vg.add_argument("--agora", help="(testes) instante da verificação, ISO 8601")
    ts = al_sub.add_parser("teste", help="abre/encerra um alerta fictício para testar o recebimento do e-mail")
    ts.add_argument("modo", choices=["abrir", "encerrar"])
    for q in (ex, vg, ts):
        q.add_argument("--simular", action="store_true", help="mostra as ações sem alterar issues")
    args = ap.parse_args(argv)

    if args.cmd == "reconstruir-logs":
        lg.reconstruir_visoes(list(ORDEM_EXECUCAO))
        print("Visões de log reconstruídas.")
        return 0
    if args.cmd == "alertas":
        return _alertas(args)
    if args.cmd == "semear":
        res = executar(None, gatilho="manual", coletores=coletores_legado(args.legado))
    else:
        res = executar(args.indice, gatilho=args.gatilho, dry_run=args.dry_run,
                       pular_se_recente_min=args.pular_se_recente)
    _resumo_actions(res, args)
    if res.get("pulada"):
        return 0
    print("\nResumo:")
    for e in res["entradas"]:
        print(f"  {e['indice']:6} {e['resultado']:8} {e['duracao_ms']:>6} ms  {e['mensagem']}")
    return 1 if any(e["resultado"] == "erro" for e in res["entradas"]) else 0


if __name__ == "__main__":
    sys.exit(main())
