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
BUSCA = "https://www.ipea.gov.br/cartadeconjuntura/?s=ICTI"      # poucos resultados, mais recente primeiro
MAX_PAGINAS = 8
# Título aceito: "Índice de Custo da Tecnologia da Informação (ICTI) <sep> <mês> de <ano>", com <sep> = hífen,
# travessão ou dois-pontos (o Ipea usou "(ICTI): setembro de 2026" em nov/2026; travessões são normalizados em _norm).
RE_TITULO = re.compile(
    r"indice de custos? da tecnologia da informacao(?:\s*\(icti\))?\s*[-:]\s*([a-z]+)\s+de\s+(\d{4})")
RE_BASE = re.compile(r"\(\s*([a-zç]{3})/(\d{4})\s*=\s*100\s*\)", re.I)


def _norm(t: str) -> str:
    t = (t or "").replace("–", "-").replace("—", "-").replace("−", "-")
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", t).strip()


# --------------------------------------------------------------------------- descoberta
def _titulos(html: str, base_url: str):
    """Títulos com link em ordem de página (blog e busca: mais recente primeiro), com ou sem <article>."""
    soup = BeautifulSoup(html, "html.parser")
    for h in soup.find_all(["h1", "h2", "h3"]):
        a = h.find("a", href=True)
        if a:
            yield h.get_text(" ", strip=True), urljoin(base_url, a["href"])


def achar_posts(html: str, base_url: str = BLOG) -> list[dict]:
    """Posts do ICTI numa página do blog ou da busca: [{titulo, competencia, url}]."""
    achados = []
    for titulo, url in _titulos(html, base_url):
        m = RE_TITULO.search(_norm(titulo))
        mes = mes_por_nome(m.group(1)) if m else None
        if mes:
            achados.append({"titulo": titulo, "competencia": competencia(int(m.group(2)), mes), "url": url})
    return achados


def titulos_icti_nao_reconhecidos(html: str, base_url: str = BLOG) -> list[str]:
    """Títulos que citam o ICTI, aparecem ANTES do primeiro post reconhecido (isto é, são mais recentes) e que
    o padrão estrito não reconheceu: sinal de que o Ipea mudou o formato do título."""
    nao = []
    for titulo, _ in _titulos(html, base_url):
        n = _norm(titulo)
        if RE_TITULO.search(n) and mes_por_nome(RE_TITULO.search(n).group(1)):
            break
        if "icti" in n or "tecnologia da informacao" in n:
            nao.append(titulo)
    return nao


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
    def _por_busca(self, log: Logger, avisos: list[str]) -> dict | None:
        """Caminho curto: página de busca do blog (poucos resultados). None => usar a navegação do blog."""
        log("Procurando publicação do ICTI pela busca do blog", url=BUSCA)
        try:
            r = obter(self.sessao, BUSCA, log=log, **self._kw)
        except ErroColeta as e:
            log(f"Busca do blog indisponível ({e}); usando a navegação paginada", nivel="aviso")
            return None
        posts = achar_posts(r.text, BUSCA)
        for t in titulos_icti_nao_reconhecidos(r.text, BUSCA):
            avisos.append(f"Publicação com ICTI no título não reconhecida (formato mudou?): {t!r}")
            log(avisos[-1], nivel="aviso")
        if not posts:
            log("Busca sem resultados reconhecidos; usando a navegação paginada", nivel="aviso")
            return None
        return max(posts, key=lambda p: p["competencia"])

    def _por_navegacao(self, log: Logger, avisos: list[str]) -> dict:
        todos = []
        for pagina in range(1, MAX_PAGINAS + 1):
            url = BLOG if pagina == 1 else f"{BLOG}/page/{pagina}/"
            log(f"Procurando publicação do ICTI no blog (página {pagina})", url=url)
            r = obter(self.sessao, url, log=log, **self._kw)
            todos += achar_posts(r.text, url)
            if pagina == 1:
                for t in titulos_icti_nao_reconhecidos(r.text, url):
                    if not any(t in a for a in avisos):
                        avisos.append(f"Publicação com ICTI no título não reconhecida (formato mudou?): {t!r}")
                        log(avisos[-1], nivel="aviso")
            if todos:
                break
        if not todos:
            raise ErroEstrutura(f"Nenhuma publicação do ICTI encontrada nas {MAX_PAGINAS} primeiras "
                                "páginas do blog (layout alterado ou publicação fora do período)")
        return max(todos, key=lambda p: p["competencia"])

    def _localizar_post(self, log: Logger, avisos: list[str]) -> dict:
        post = self._por_busca(log, avisos) or self._por_navegacao(log, avisos)
        log(f"Publicação encontrada: {post['titulo']}", url=post["url"], competencia=post["competencia"])
        return post

    def coletar(self, log: Logger) -> ResultadoColeta:
        avisos: list[str] = []
        post = self._localizar_post(log, avisos)
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
        if ult != post["competencia"]:
            avisos.append(f"Última competência da planilha ({ult}) difere da anunciada no título do post "
                          f"({post['competencia']})")
            log(avisos[-1], nivel="aviso")
        log(f"{len(regs)} competências lidas (até {ult})", registros=len(regs))
        return ResultadoColeta(regs, {"metodo": "Download direto da planilha da série completa",
                                      "url": link, "arquivo": nome, "post": post["url"]}, base, avisos)
