"""Interface de linha de comando.

  python -m indices atualizar [--indice ipca --indice ist] [--dry-run] [--gatilho agendado]
                              [--pular-se-recente 120]
  python -m indices semear --legado <pasta com *_bruto.xlsx>
  python -m indices reconstruir-logs
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import logs as lg
from .config import INDICES, ORDEM_EXECUCAO, dir_dados
from .migracao import coletores_legado
from .orquestrador import executar


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
    args = ap.parse_args(argv)

    if args.cmd == "reconstruir-logs":
        lg.reconstruir_visoes(list(ORDEM_EXECUCAO))
        print("Visões de log reconstruídas.")
        return 0
    if args.cmd == "semear":
        res = executar(None, gatilho="manual", coletores=coletores_legado(args.legado))
    else:
        res = executar(args.indice, gatilho=args.gatilho, dry_run=args.dry_run,
                       pular_se_recente_min=args.pular_se_recente)
    if res.get("pulada"):
        return 0
    print("\nResumo:")
    for e in res["entradas"]:
        print(f"  {e['indice']:6} {e['resultado']:8} {e['duracao_ms']:>6} ms  {e['mensagem']}")
    return 1 if any(e["resultado"] == "erro" for e in res["entradas"]) else 0


if __name__ == "__main__":
    sys.exit(main())
