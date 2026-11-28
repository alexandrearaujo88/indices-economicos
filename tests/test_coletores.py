"""Coletores de ponta a ponta com HTTP simulado (sem rede)."""
import openpyxl
import pytest
import requests

from conftest import FakeResp, FakeSession, html_ist, ler_xlsx_bytes, zip_com
from indices.coletores import ColetorICTI, ColetorIPCA, ColetorIST
from indices.coletores import icti as m_icti, ipca as m_ipca, ist as m_ist
from indices.config import INDICES
from indices.http import obter
from indices.modelos import ErroColeta, ErroEstrutura
from test_parsers import BLOG, sidra_json


def mudo(*a, **k):
    pass


def sidra_real():
    """JSON do SIDRA reconstruído a partir da planilha original."""
    regs, _ = m_ipca.parse_linhas_ipca(m_ipca._linhas_xlsx(ler_xlsx_bytes("ipca_bruto.xlsx")), "1994-01")
    return sidra_json({r.competencia: (r.indice, r.var_mes, r.var_12m) for r in regs})


# ------------------------------------------------------------------ HTTP
def test_obter_repete_em_erro_transitorio_e_respeita_espera(dormir):
    s = FakeSession({"https://x/": [FakeResp(status=503), requests.exceptions.ConnectTimeout(), FakeResp(b"ok")]})
    assert obter(s, "https://x/a", dormir=dormir).content == b"ok"
    assert s.n("https://x/") == 3 and dormir.__self__ == [2.0, 6.0]


def test_obter_nao_repete_404(dormir):
    s = FakeSession({"https://x/": FakeResp(status=404)})
    with pytest.raises(ErroColeta, match="HTTP 404"):
        obter(s, "https://x/a", dormir=dormir)
    assert s.n("https://x/") == 1


def test_obter_esgota_tentativas(dormir):
    s = FakeSession({"https://x/": requests.exceptions.ReadTimeout()})
    with pytest.raises(ErroColeta, match="3 tentativas"):
        obter(s, "https://x/a", dormir=dormir)
    assert s.n("https://x/") == 3


def test_obter_tls_so_cai_para_inseguro_se_permitido(dormir):
    erro = requests.exceptions.SSLError("cadeia incompleta")
    s = FakeSession({"https://x/": [erro, FakeResp(b"ok")]})
    avisos = []
    r = obter(s, "https://x/a", dormir=dormir, permitir_tls_inseguro=True, log=lambda m, nivel="info", **k: avisos.append((nivel, m)))
    assert r.content == b"ok" and [c["verify"] for c in s.chamadas] == [True, False]
    assert any(n == "aviso" and "SEM verificação" in m for n, m in avisos)
    s2 = FakeSession({"https://x/": erro})
    with pytest.raises(ErroColeta):
        obter(s2, "https://x/a", dormir=dormir, permitir_tls_inseguro=False)
    assert all(c["verify"] for c in s2.chamadas)


# ------------------------------------------------------------------ IPCA
def test_ipca_fonte_primaria_e_o_ftp(dormir):
    zipado = zip_com("ipca_SerieHist.xlsx", ler_xlsx_bytes("ipca_bruto.xlsx"))
    s = FakeSession({m_ipca.URL_FTP: FakeResp(zipado)})
    res = ColetorIPCA(INDICES["ipca"], s, dormir).coletar(mudo)
    assert len(res.registros) == 393 and res.base_detectada == "dez/1993 = 100" and not res.avisos
    assert s.n("https://apisidra") == 0


def test_ipca_contingencia_sidra_quando_ftp_falha(dormir):
    s = FakeSession({m_ipca.URL_FTP: FakeResp(status=500), "https://apisidra.ibge.gov.br": FakeResp(json_obj=sidra_real())})
    msgs = []
    res = ColetorIPCA(INDICES["ipca"], s, dormir).coletar(lambda m, nivel="info", **k: msgs.append((nivel, m)))
    assert len(res.registros) == 393 and any("contingência" in a for a in res.avisos)
    assert s.n(m_ipca.URL_FTP) == 3
    assert sum(1 for n, m in msgs if n == "aviso" and "contingência" in m) >= 1


def test_ipca_sidra_bloqueado_403_nao_e_consultado_se_ftp_ok(dormir):
    zipado = zip_com("a.xlsx", ler_xlsx_bytes("ipca_bruto.xlsx"))
    s = FakeSession({m_ipca.URL_FTP: FakeResp(zipado), "https://apisidra.ibge.gov.br": FakeResp(status=403)})
    assert len(ColetorIPCA(INDICES["ipca"], s, dormir).coletar(mudo).registros) == 393
    assert s.n("https://apisidra") == 0


def test_ipca_falha_nas_duas_fontes(dormir):
    s = FakeSession({"https://apisidra.ibge.gov.br": FakeResp(status=403), m_ipca.URL_FTP: requests.exceptions.ConnectionError()})
    with pytest.raises(ErroColeta):
        ColetorIPCA(INDICES["ipca"], s, dormir).coletar(mudo)


def test_ipca_ftp_com_arquivo_invalido_cai_na_contingencia(dormir):
    s = FakeSession({m_ipca.URL_FTP: FakeResp(b"<html>manutencao</html>"), "https://apisidra.ibge.gov.br": FakeResp(json_obj=sidra_real())})
    assert len(ColetorIPCA(INDICES["ipca"], s, dormir).coletar(mudo).registros) == 393


# ------------------------------------------------------------------ IST
def html_ist_real():
    ws = openpyxl.load_workbook("tests/fixtures/ist_bruto.xlsx", read_only=True).worksheets[0]
    return html_ist([(r[0], r[1], r[2]) for r in list(ws.iter_rows(values_only=True))[1:]])


def test_ist_coleta(dormir):
    s = FakeSession({m_ist.URL: FakeResp(html_ist_real())})
    res = ColetorIST(INDICES["ist"], s, dormir).coletar(mudo)
    assert len(res.registros) == 249


def test_ist_certificado_invalido_usa_fallback_com_aviso(dormir):
    s = FakeSession({m_ist.URL: [requests.exceptions.SSLError("x"), FakeResp(html_ist_real())]})
    msgs = []
    res = ColetorIST(INDICES["ist"], s, dormir).coletar(lambda m, nivel="info", **k: msgs.append((nivel, m)))
    assert len(res.registros) == 249 and any(n == "aviso" and "TLS" in m for n, m in msgs)


def test_ist_pagina_bloqueada(dormir):
    s = FakeSession({m_ist.URL: FakeResp("<html><title>Access Denied</title></html>")})
    with pytest.raises(ErroEstrutura, match="Access Denied"):
        ColetorIST(INDICES["ist"], s, dormir).coletar(mudo)


def test_ist_timeout_total(dormir):
    s = FakeSession({m_ist.URL: requests.exceptions.ConnectTimeout()})
    with pytest.raises(ErroColeta, match="tempo esgotado"):
        ColetorIST(INDICES["ist"], s, dormir).coletar(mudo)


# ------------------------------------------------------------------ ICTI
POST = """<html><body><div class="entry-content"><a href="https://www.ipea.gov.br/cartadeconjuntura/wp-content/uploads/2026/10/261015_cc_72.pdf">Acesse o texto completo</a>
<a href="https://www.ipea.gov.br/cartadeconjuntura/wp-content/uploads/2026/10/261015_ICTI_serie_completa_agosto_26.xlsx">Dados Xls</a></div></body></html>"""
URL_POST = "https://www.ipea.gov.br/cartadeconjuntura/index.php/2026/10/icti-agosto/"
URL_XLSX = "https://www.ipea.gov.br/cartadeconjuntura/wp-content/uploads/2026/10/261015_ICTI_serie_completa_agosto_26.xlsx"


def rotas_icti(xlsx=None, pagina1=BLOG, post=POST, busca=None):
    return {m_icti.BLOG + "/page/": FakeResp(pagina1), m_icti.BLOG: FakeResp(pagina1), URL_POST: FakeResp(post),
            m_icti.BUSCA: busca if busca is not None else FakeResp(pagina1),
            URL_XLSX: FakeResp(xlsx if xlsx is not None else ler_xlsx_bytes("icti_bruto.xlsx"))}


def test_icti_fluxo_completo(dormir):
    s = FakeSession(rotas_icti())
    res = ColetorICTI(INDICES["icti"], s, dormir).coletar(mudo)
    assert len(res.registros) == 164 and res.fonte["arquivo"].endswith("agosto_26.xlsx")
    assert res.registros[-1].competencia == "2026-08" and not res.avisos


VAZIA = "<html><body><article><h1 class='entry-title'><a href='/x'>Outro tema</a></h1></article></body></html>"
BUSCA_SET = """<html><body><h1><a href="/cartadeconjuntura/index.php/2026/11/icti-setembro/">Índice de Custo da Tecnologia da Informação (ICTI): setembro de 2026</a></h1>
<h1><a href="/cartadeconjuntura/index.php/2026/10/icti-agosto/">Índice de Custo da Tecnologia da Informação (ICTI) – agosto de 2026</a></h1></body></html>"""
URL_POST_SET = "https://www.ipea.gov.br/cartadeconjuntura/index.php/2026/11/icti-setembro/"
URL_XLSX_SET = "https://www.ipea.gov.br/cartadeconjuntura/wp-content/uploads/2026/11/261117_ICTI_série_completa_setembro_26.xlsx"
POST_SET = f"""<html><body><div class="entry-content"><a href="{URL_XLSX_SET}">Dados Xls</a></div></body></html>"""


def test_icti_usa_a_busca_e_reconhece_titulo_com_dois_pontos(dormir):
    # caso real de nov/2026: "(ICTI): setembro de 2026" (dois-pontos) escapava do padrão e a coleta ficava em agosto
    rotas = rotas_icti(busca=FakeResp(BUSCA_SET))
    rotas[URL_POST_SET] = FakeResp(POST_SET)
    rotas[URL_XLSX_SET] = FakeResp(ler_xlsx_bytes("icti_bruto.xlsx"))
    s = FakeSession(rotas)
    res = ColetorICTI(INDICES["icti"], s, dormir).coletar(mudo)
    assert res.fonte["post"] == URL_POST_SET and res.fonte["arquivo"].endswith("setembro_26.xlsx")
    assert s.n(m_icti.BUSCA) == 1 and not [c for c in s.chamadas if c["url"] == m_icti.BLOG]   # sem navegar pelo blog
    assert any("difere da anunciada" in a for a in res.avisos)           # fixture termina em ago/2026: aviso esperado


def test_icti_busca_indisponivel_cai_na_navegacao_do_blog(dormir):
    rotas = rotas_icti()
    rotas[m_icti.BUSCA] = requests.exceptions.ConnectTimeout()
    msgs = []
    res = ColetorICTI(INDICES["icti"], FakeSession(rotas), dormir).coletar(lambda m, nivel="info", **k: msgs.append((nivel, m)))
    assert len(res.registros) == 164 and any(n == "aviso" and "Busca do blog indisponível" in m for n, m in msgs)


def test_icti_busca_sem_resultados_cai_na_navegacao_do_blog(dormir):
    rotas = rotas_icti(busca=FakeResp(VAZIA))
    assert len(ColetorICTI(INDICES["icti"], FakeSession(rotas), dormir).coletar(mudo).registros) == 164


def test_icti_titulo_mais_novo_nao_reconhecido_gera_aviso(dormir):
    novo = BLOG.replace("<article>", '<article><h1 class="entry-title"><a href="/x">ICTI | setembro 2026 (nova nota)</a></h1></article>\n<article>', 1)
    res = ColetorICTI(INDICES["icti"], FakeSession(rotas_icti(busca=FakeResp(novo))), dormir).coletar(mudo)
    assert any("não reconhecida" in a and "setembro 2026" in a for a in res.avisos)


def test_icti_procura_nas_proximas_paginas(dormir):
    vazia = VAZIA
    rotas = rotas_icti(busca=FakeResp(VAZIA))
    rotas[m_icti.BLOG] = FakeResp(vazia)
    rotas[m_icti.BLOG + "/page/2/"] = FakeResp(BLOG)
    s = FakeSession(rotas)
    assert len(ColetorICTI(INDICES["icti"], s, dormir).coletar(mudo).registros) == 164


def test_icti_sem_publicacao(dormir):
    s = FakeSession({m_icti.BLOG: FakeResp(VAZIA), m_icti.BUSCA: FakeResp(VAZIA)})
    with pytest.raises(ErroEstrutura, match="Nenhuma publicação"):
        ColetorICTI(INDICES["icti"], s, dormir).coletar(mudo)
    assert s.n(m_icti.BLOG) == m_icti.MAX_PAGINAS


def test_icti_arquivo_corrompido(dormir):
    s = FakeSession(rotas_icti(xlsx=b"<html>erro</html>"))
    with pytest.raises(ErroEstrutura, match="não é uma planilha"):
        ColetorICTI(INDICES["icti"], s, dormir).coletar(mudo)


def test_icti_timeout_na_blog(dormir):
    s = FakeSession({m_icti.BLOG: requests.exceptions.ConnectTimeout(), m_icti.BUSCA: requests.exceptions.ConnectTimeout()})
    with pytest.raises(ErroColeta):
        ColetorICTI(INDICES["icti"], s, dormir).coletar(mudo)
