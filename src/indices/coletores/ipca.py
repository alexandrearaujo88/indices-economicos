"""Coletor do IPCA (IBGE).

Fonte primária: planilha 'ipca_SerieHist.zip' do FTP do IBGE (XLS/XLSX), lida em memória.
Contingência: API SIDRA, tabela 1737 (JSON): v 2266 = número-índice (dez/1993 = 100),
v 63 = variação mensal (%), v 2265 = variação em 12 meses (%).
"""
from __future__ import annotations

import io
import re
import zipfile

from ..config import IndiceCfg
from ..http import obter
from ..modelos import ErroColeta, ErroEstrutura, Registro, ResultadoColeta, num_br
from ..tempo import competencia, mes_por_nome
from .base import Coletor, Logger

URL_SIDRA = ("https://apisidra.ibge.gov.br/values/t/1737/n1/all/v/2266,63,2265/p/all"
             "/d/v2266%204,v63%202,v2265%202")
URL_FTP = ("https://ftp.ibge.gov.br/Precos_Indices_de_Precos_ao_Consumidor/IPCA/"
           "Serie_Historica/ipca_SerieHist.zip")
VARIAVEIS = {"2266": "indice", "63": "var_mes", "2265": "var_12m"}
BASE_PADRAO = "dez/1993 = 100"


# --------------------------------------------------------------------------- parsers
def parse_sidra(dados, inicio_serie: str | None = None) -> tuple[list[Registro], str | None]:
    """Converte a resposta JSON do SIDRA (lista; 1ª linha = cabeçalho) em registros."""
    if not isinstance(dados, list) or len(dados) < 2 or not isinstance(dados[0], dict):
        raise ErroEstrutura("Resposta do SIDRA em formato inesperado (esperada lista com cabeçalho)")
    cab = dados[0]
    if not all(k in cab for k in ("V", "D2C", "D3C")):
        raise ErroEstrutura("Cabeçalho do SIDRA sem as chaves V/D2C/D3C: estrutura da API mudou")
    por_periodo: dict[str, dict] = {}
    base = None
    for linha in dados[1:]:
        var = VARIAVEIS.get(str(linha.get("D2C")))
        if var is None:
            raise ErroEstrutura(f"Variável inesperada no SIDRA: {linha.get('D2C')!r}")
        per = str(linha.get("D3C", ""))
        if not re.fullmatch(r"\d{6}", per):
            raise ErroEstrutura(f"Período inesperado no SIDRA: {per!r}")
        comp = competencia(int(per[:4]), int(per[4:]))
        try:
            val = num_br(linha.get("V"))
        except ValueError:
            val = None
        por_periodo.setdefault(comp, {})[var] = val
        if var == "indice" and base is None:
            m = re.search(r"base:\s*([a-zç]+)\s+de\s+(\d{4})\s*=\s*100", str(linha.get("D2N", "")), re.I)
            if m:
                mes = mes_por_nome(m.group(1))
                if mes:
                    base = f"{['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez'][mes-1]}/{m.group(2)} = 100"
    regs = []
    for comp in sorted(por_periodo):
        if inicio_serie and comp < inicio_serie:
            continue
        v = por_periodo[comp]
        if v.get("indice") is None:
            continue    # competência sem número-índice (ainda não divulgada)
        regs.append(Registro(comp, v["indice"], v.get("var_mes"), v.get("var_12m")))
    return regs, base


def _linhas_xlsx(conteudo: bytes) -> list[list]:
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _linhas_xls(conteudo: bytes) -> list[list]:
    try:
        import xlrd
    except ImportError as e:  # pragma: no cover
        raise ErroColeta("Biblioteca 'xlrd' ausente: necessária para ler o .xls da contingência do IBGE") from e
    wb = xlrd.open_workbook(file_contents=conteudo)
    ws = wb.sheet_by_index(0)
    return [ws.row_values(i) for i in range(ws.nrows)]


def parse_linhas_ipca(linhas: list[list], inicio_serie: str | None = None) -> tuple[list[Registro], str | None]:
    """Interpreta a planilha 'Série histórica do IPCA' do IBGE (linhas já lidas)."""
    cab = None
    for i, l in enumerate(linhas):
        if l and str(l[0] or "").strip().upper() == "ANO" and len(l) > 3:
            cab = i
            break
    if cab is None:
        raise ErroEstrutura("Cabeçalho 'ANO' não encontrado na planilha do IBGE")
    head = [str(c or "").strip().upper() for c in linhas[cab]]
    try:
        c_mes = head.index("MÊS") if "MÊS" in head else head.index("MES")
        c_idx = next(i for i, h in enumerate(head) if "ÍNDICE" in h or "INDICE" in h)
        c_var = head.index("(%)")
    except (ValueError, StopIteration) as e:
        raise ErroEstrutura("Colunas MÊS/NÚMERO ÍNDICE/(%) não encontradas na planilha do IBGE") from e
    c_12m = c_var + 4
    sub = [str(c or "").strip().upper() for c in (linhas[cab + 1] if cab + 1 < len(linhas) else [])]
    sub += [""] * (c_12m + 1 - len(sub))
    if str(sub[c_12m]).replace(".0", "") != "12":
        raise ErroEstrutura("Coluna de variação em 12 meses não está na posição esperada da planilha do IBGE")
    base = None
    mb = re.search(r"DEZ\s*93\s*=\s*100", " ".join(sub))
    if mb:
        base = BASE_PADRAO
    regs, ano = [], None
    for l in linhas[cab + 1:]:
        l = list(l) + [None] * (c_12m + 1 - len(l))
        if l[0] not in (None, ""):
            try:
                ano = int(float(str(l[0]).strip()))
            except ValueError:
                continue    # linha de texto (rodapé/fonte)
        mes = mes_por_nome(str(l[c_mes] or ""))
        if mes is None or ano is None:
            continue
        try:
            idx = num_br(l[c_idx])
        except ValueError:
            continue
        if idx is None:
            continue
        comp = competencia(ano, mes)
        if inicio_serie and comp < inicio_serie:
            continue
        regs.append(Registro(comp, idx, num_br(l[c_var]), num_br(l[c_12m])))
    regs.sort(key=lambda r: r.competencia)
    return regs, base


def parse_zip_ftp(conteudo: bytes, inicio_serie: str | None = None):
    try:
        with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
            nomes = [n for n in z.namelist() if n.lower().endswith((".xls", ".xlsx"))]
            if not nomes:
                raise ErroEstrutura("Nenhum arquivo XLS/XLSX dentro do ZIP do IBGE")
            nome = nomes[0]
            dados = z.read(nome)
    except zipfile.BadZipFile as e:
        raise ErroEstrutura("O arquivo baixado do IBGE não é um ZIP válido") from e
    try:
        linhas = _linhas_xlsx(dados) if nome.lower().endswith(".xlsx") else _linhas_xls(dados)
    except ErroColeta:
        raise
    except Exception as e:                      # planilha corrompida/ilegível (xlrd/openpyxl)
        raise ErroEstrutura(f"Planilha do IBGE ilegível ({type(e).__name__})") from e
    regs, base = parse_linhas_ipca(linhas, inicio_serie)
    return regs, base, nome


# --------------------------------------------------------------------------- coletor
class ColetorIPCA(Coletor):
    """Fonte primária: planilha da série histórica no FTP do IBGE (método original, validado em
    execução real no GitHub Actions). Contingência: API SIDRA (JSON), que o IBGE pode bloquear
    para servidores de nuvem (HTTP 403 observado no GitHub Actions)."""

    def _ftp(self, log: Logger) -> ResultadoColeta:
        log("Baixando planilha da série histórica (FTP do IBGE)", url=URL_FTP)
        r = obter(self.sessao, URL_FTP, log=log, **self._kw)
        regs, base, nome = parse_zip_ftp(r.content, self.cfg.inicio_serie)
        if not regs:
            raise ErroEstrutura("Planilha do IBGE sem competências válidas")
        log(f"Planilha {nome}: {len(regs)} competências lidas", registros=len(regs), arquivo=nome)
        return ResultadoColeta(regs, {"metodo": "Planilha FTP do IBGE", "url": URL_FTP, "arquivo": nome}, base, [])

    def _sidra(self, log: Logger) -> ResultadoColeta:
        log("Consultando API SIDRA do IBGE (tabela 1737)", url=URL_SIDRA.split("/values")[0])
        r = obter(self.sessao, URL_SIDRA, log=log, **self._kw)
        try:
            dados = r.json()
        except ValueError as e:
            raise ErroEstrutura("Resposta do SIDRA não é JSON válido") from e
        regs, base = parse_sidra(dados, self.cfg.inicio_serie)
        if not regs:
            raise ErroEstrutura("SIDRA não retornou nenhuma competência com número-índice")
        log(f"SIDRA: {len(regs)} competências lidas", registros=len(regs))
        return ResultadoColeta(regs, {"metodo": "API SIDRA (contingência)", "url": URL_SIDRA.split("/p/")[0]},
                               base or BASE_PADRAO, [])

    def coletar(self, log: Logger) -> ResultadoColeta:
        try:
            return self._ftp(log)
        except ErroColeta as e:
            aviso = f"Fonte primária (planilha do FTP do IBGE) falhou: {e}. Usando a API SIDRA como contingência"
            log(aviso, nivel="aviso")
        res = self._sidra(log)
        res.avisos.append("Dados obtidos pela fonte de contingência (API SIDRA)")
        log(res.avisos[-1], nivel="aviso")
        res.avisos.insert(0, aviso)
        return res
