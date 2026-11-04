"""Coletor do ICTI (Ipea): localiza a publicação mais recente e baixa a planilha da série completa."""
from __future__ import annotations

import datetime as dt
import io
import re
import unicodedata
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..http import obter
from ..modelos import ErroColeta, ErroEstrutura, Registro, ResultadoColeta, num_br
from ..tempo import competencia, mes_por_nome
from .base import Coletor, Logger

BLOG = "https://www.ipea.gov.br/cartadeconjuntura/index.php"
MAX_PAGINAS = 8
RE_TITULO = re.compile(
    r"indice de custos? da tecnologia da informacao(?:\s*\(icti\))?\s*[-–]\s*([a-z]+)\s+de\s+(\d{4})")
RE_BASE = re.compile(r"\(\s*([a-zç]{3})/(\d{4})\s*=\s*100\s*\)", re.I)


def _norm(t: str) -> str:
    t = (t or "").replace("–", "-").replace("—", "-").replace("−", "-")
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", t).strip()


# --------------------------------------------------------------------------- descoberta
def achar_posts(html: str, base_url: str = BLOG) -> list[dict]:
    """Posts do ICTI numa página do blog: [{titulo, competencia, url}], título estrito
    ('Índice de Custo da Tecnologia da Informação (ICTI) – <mês> de <ano>')."""
    soup = BeautifulSoup(html, "html.parser")
    achados = []
    for art in soup.find_all("article"):
        h = art.find(["h1", "h2"], class_="entry-title") or art.find(["h1", "h2"])
        if not h:
            continue
        titulo = h.get_text(" ", strip=True)
        m = RE_TITULO.search(_norm(titulo))
        if not m:
            continue
        mes = mes_por_nome(m.group(1))
        if not mes:
            continue
        a = h.find("a", href=True)
        if not a:
            continue
        achados.append({"titulo": titulo, "competencia": competencia(int(m.group(2)), mes),
                        "url": urljoin(base_url, a["href"])})
    return achados


def achar_link_planilha(html: str, base_url: str) -> str:
    """Link da planilha da série completa dentro do post."""
    soup = BeautifulSoup(html, "html.parser")
    corpo = soup.find(class_="entry-content") or soup
    links = []
    for a in corpo.find_all("a", href=True):
        href = a["href"].split("#")[0]
        if href.lower().split("?")[0].endswith((".xlsx", ".xls")):
            links.append((a.get_text(" ", strip=True), urljoin(base_url, href)))
    if not links:
        raise ErroEstrutura("Nenhuma planilha XLSX/XLS encontrada no post do ICTI")

    def pontos(item):
        texto, href = item
        h = href.lower()
        return -(("icti" in h) * 2 + ("serie" in h or "completa" in h) + ("xls" in texto.lower() or "dados" in texto.lower()))
    links.sort(key=pontos)
    return links[0][1]


# --------------------------------------------------------------------------- planilha
def parse_xlsx(conteudo: bytes, inicio_serie: str | None = None) -> tuple[list[Registro], str | None]:
    if not conteudo.startswith(b"PK\x03\x04"):
        raise ErroEstrutura("O arquivo baixado não é uma planilha XLSX válida (assinatura ausente)")
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
    ws = wb["Dados"] if "Dados" in wb.sheetnames else wb[wb.sheetnames[0]]
    linhas = [list(r) for r in ws.iter_rows(values_only=True)]
    cab = None
    for i, l in enumerate(linhas[:15]):
        if len(l) > 1 and _norm(str(l[0] or "")) in ("mes",) and _norm(str(l[1] or "")).startswith("icti"):
            cab = i
            break
    if cab is None:
        raise ErroEstrutura("Cabeçalho 'Mês | ICTI (...)' não encontrado na planilha do Ipea")
    head = linhas[cab]
    mb = RE_BASE.search(str(head[1]))
    base = None
    if mb:
        mes = mes_por_nome(mb.group(1))
        if mes:
            base = f"{mb.group(1).lower()}/{mb.group(2)} = 100"
    cols = {"mes": 0, "idx": 1}
    for j, h in enumerate(head):
        n = _norm(str(h or ""))
        if n.startswith("var. no mes"):
            cols["m"] = j
        elif n.startswith("var. em 12 meses"):
            cols["a12"] = j
    if "m" not in cols or "a12" not in cols:
        raise ErroEstrutura("Colunas de variação mensal/12 meses não encontradas na planilha do Ipea")
    regs = []
    for l in linhas[cab + 1:]:
        l = list(l) + [None] * 6
        mes = l[cols["mes"]]
        if mes is None or str(mes).strip() == "":
            continue
        if isinstance(mes, (dt.datetime, dt.date)):
            comp = competencia(mes.year, mes.month)
        else:
            try:
                d = dt.date.fromisoformat(str(mes).strip()[:10])
            except ValueError:
                continue
            comp = competencia(d.year, d.month)
        if inicio_serie and comp < inicio_serie:
            continue
        try:
            idx = num_br(l[cols["idx"]])
            m = num_br(l[cols["m"]])
            a12 = num_br(l[cols["a12"]])
        except ValueError as e:
            raise ErroEstrutura(f"Valor não numérico na planilha do Ipea ({comp}): {e}") from e
        if idx is None:
            continue
        regs.append(Registro(comp, idx, m, a12))
    regs.sort(key=lambda r: r.competencia)
    return regs, base


# --------------------------------------------------------------------------- coletor
class ColetorICTI(Coletor):
    def _localizar_post(self, log: Logger) -> dict:
        todos = []
        for pagina in range(1, MAX_PAGINAS + 1):
            url = BLOG if pagina == 1 else f"{BLOG}/page/{pagina}/"
            log(f"Procurando publicação do ICTI no blog (página {pagina})", url=url)
            r = obter(self.sessao, url, log=log, **self._kw)
            todos += achar_posts(r.text, url)
            if todos:
                break
        if not todos:
            raise ErroEstrutura(f"Nenhuma publicação do ICTI encontrada nas {MAX_PAGINAS} primeiras "
                                "páginas do blog (layout alterado ou publicação fora do período)")
        post = max(todos, key=lambda p: p["competencia"])
        log(f"Publicação encontrada: {post['titulo']}", url=post["url"], competencia=post["competencia"])
        return post

    def coletar(self, log: Logger) -> ResultadoColeta:
        post = self._localizar_post(log)
        r = obter(self.sessao, post["url"], log=log, **self._kw)
        link = achar_link_planilha(r.text, post["url"])
        nome = link.rsplit("/", 1)[-1]
        log(f"Baixando planilha {nome}", url=link, arquivo=nome)
        arq = obter(self.sessao, link, log=log, **self._kw)
        log(f"Planilha recebida ({len(arq.content) // 1024} KB); lendo diretamente da memória")
        regs, base = parse_xlsx(arq.content, self.cfg.inicio_serie)
        if not regs:
            raise ErroEstrutura("Planilha do Ipea sem competências válidas")
        ult = regs[-1].competencia
        avisos = []
        if ult != post["competencia"]:
            avisos.append(f"Última competência da planilha ({ult}) difere da anunciada no título do post "
                          f"({post['competencia']})")
            log(avisos[-1], nivel="aviso")
        log(f"{len(regs)} competências lidas (até {ult})", registros=len(regs))
        return ResultadoColeta(regs, {"metodo": "Download direto da planilha da série completa",
                                      "url": link, "arquivo": nome, "post": post["url"]}, base, avisos)
