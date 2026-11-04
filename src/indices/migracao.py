"""Carga inicial a partir dos arquivos '*_bruto.xlsx' do projeto original (Apps Script/BAT).

Usa exatamente os mesmos parsers e a mesma validação dos coletores, apenas trocando a origem
(arquivo local em vez de download).
"""
from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from .coletores import icti as c_icti, ipca as c_ipca, ist as c_ist
from .config import INDICES
from .modelos import ResultadoColeta


class ColetorLocal:
    def __init__(self, codigo: str, arquivo: Path):
        self.codigo, self.arquivo = codigo, Path(arquivo)

    def coletar(self, log) -> ResultadoColeta:
        cfg = INDICES[self.codigo]
        log(f"Lendo arquivo do projeto original: {self.arquivo.name}", arquivo=self.arquivo.name)
        dados = self.arquivo.read_bytes()
        if self.codigo == "ipca":
            regs, base = c_ipca.parse_linhas_ipca(c_ipca._linhas_xlsx(dados), cfg.inicio_serie)
            avisos = []
        elif self.codigo == "icti":
            regs, base = c_icti.parse_xlsx(dados, cfg.inicio_serie)
            avisos = []
        else:
            ws = load_workbook(self.arquivo, read_only=True, data_only=True).worksheets[0]
            linhas = "".join(f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td></tr>"
                             for r in list(ws.iter_rows(values_only=True))[1:] if r and r[0])
            html = ("<table class='listing'><tr><th>Referência</th><th>Variação</th><th>IST</th></tr>"
                    + linhas + "</table>")
            regs, avisos = c_ist.parse_html(html)
            base = None
        log(f"{len(regs)} competências lidas do arquivo", registros=len(regs))
        return ResultadoColeta(regs, {"metodo": "Carga inicial (arquivo do projeto original)"}, base, avisos)


ARQUIVOS = {"ipca": "ipca_bruto.xlsx", "icti": "icti_bruto.xlsx", "ist": "ist_bruto.xlsx"}


def coletores_legado(pasta: Path) -> dict:
    return {c: ColetorLocal(c, Path(pasta) / nome) for c, nome in ARQUIVOS.items()}
