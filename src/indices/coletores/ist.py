"""Coletor do IST (Anatel): leitura das tabelas HTML da página oficial."""
from __future__ import annotations

import re
import unicodedata

from bs4 import BeautifulSoup

from ..http import obter
from ..modelos import ErroEstrutura, Registro, ResultadoColeta, num_br
from ..tempo import competencia, mes_por_nome
from .base import Coletor, Logger

URL = "https://www.gov.br/anatel/pt-br/regulado/competicao/tarifas-e-precos/valores-do-ist"
LIMITE_AVISOS = 5


def _norm(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return t.lower()


def tabela_parece_ist(tabela) -> bool:
    """Compatível com a estrutura antiga (class 'listing'), a nova ('slate-table-block')
    e, como último recurso, com o cabeçalho Referência/Variação/IST."""
    classes = tabela.get("class", []) or []
    if "listing" in classes or "slate-table-block" in classes:
        return True
    primeira = tabela.find("tr")
    if not primeira:
        return False
    cab = _norm(primeira.get_text(" ", strip=True))
    return "referencia" in cab and "variacao" in cab and "ist" in cab


def parse_html(html: str) -> tuple[list[Registro], list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    tabelas = [t for t in soup.find_all("table") if tabela_parece_ist(t)]
    if not tabelas:
        titulo = soup.title.get_text(" ", strip=True)[:80] if soup.title else "sem título"
        raise ErroEstrutura("Nenhuma tabela do IST encontrada na página (layout alterado ou acesso "
                            f"bloqueado). Título da página: {titulo!r}")
    regs: list[Registro] = []
    ignoradas: list[str] = []
    for tabela in tabelas:
        linhas = tabela.find_all("tr")
        for tr in linhas[1:]:                       # linha 0 = cabeçalho
            cols = tr.find_all(["td", "th"])
            if len(cols) < 3:
                continue                            # notas de rodapé etc.
            ref = cols[0].get_text(" ", strip=True)
            limpo = re.sub(r"[^A-Za-z0-9/]", "", ref)
            partes = limpo.split("/")
            if len(partes) != 2:
                ignoradas.append(f"referência inesperada {ref!r}")
                continue
            mes = mes_por_nome(partes[0])
            ano_txt = partes[1]
            if mes is None or not ano_txt.isdigit() or len(ano_txt) not in (2, 4):
                ignoradas.append(f"referência inválida {ref!r}")
                continue
            ano = 2000 + int(ano_txt) if len(ano_txt) == 2 else int(ano_txt)
            try:
                var = num_br(cols[1].get_text(" ", strip=True))
                idx = num_br(cols[2].get_text(" ", strip=True))
            except ValueError as e:
                ignoradas.append(f"{ref}: {e}")
                continue
            if idx is None:
                ignoradas.append(f"{ref}: IST vazio")
                continue
            regs.append(Registro(competencia(ano, mes), idx, var, None))
    avisos = []
    if ignoradas:
        amostra = "; ".join(ignoradas[:LIMITE_AVISOS])
        avisos.append(f"{len(ignoradas)} linha(s) da tabela ignoradas: {amostra}")
    return regs, avisos


class ColetorIST(Coletor):
    def coletar(self, log: Logger) -> ResultadoColeta:
        log("Acessando página oficial de valores do IST (Anatel)", url=URL)
        r = obter(self.sessao, URL, log=log, permitir_tls_inseguro=self.cfg.tls_inseguro_permitido,
                  **self._kw)
        log(f"Página recebida ({len(r.content) // 1024} KB); extraindo tabelas")
        regs, avisos = parse_html(r.text)
        for a in avisos:
            log(a, nivel="aviso")
        log(f"{len(regs)} linhas válidas extraídas", registros=len(regs))
        return ResultadoColeta(regs, {"metodo": "Tabelas HTML da página oficial", "url": URL}, None, avisos)
