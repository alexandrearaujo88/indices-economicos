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
def test_ipca_fonte_primaria(dormir):
    s = FakeSession({m_ipca.URL_SIDRA.split("/values")[0]: FakeResp(json_obj=sidra_real())})
    res = ColetorIPCA(INDICES["ipca"], s, dormir).coletar(mudo)
    assert len(res.registros) == 393 and res.base_detectada == "dez/1993 = 100" and not res.avisos
    assert s.n(m_ipca.URL_FTP) == 0


def test_ipca_contingencia_quando_sidra_falha(dormir):
    zipado = zip_com("ipca_SerieHist.xlsx", ler_xlsx_bytes("ipca_bruto.xlsx"))
    s = FakeSession({"https://apisidra.ibge.gov.br": FakeResp(status=500), m_ipca.URL_FTP: FakeResp(zipado)})
    res = ColetorIPCA(INDICES["ipca"], s, dormir).coletar(mudo)
    assert len(res.registros) == 393 and any("contingência" in a for a in res.avisos)
    assert s.n("https://apisidra") == 3


def test_ipca_falha_nas_duas_fontes(dormir):
    s = FakeSession({"https://apisidra.ibge.gov.br": FakeResp(status=500), m_ipca.URL_FTP: requests.exceptions.ConnectionError()})
    with pytest.raises(ErroColeta):
        ColetorIPCA(INDICES["ipca"], s, dormir).coletar(mudo)


def test_ipca_sidra_com_json_invalido_cai_na_contingencia(dormir):
    zipado = zip_com("a.xlsx", ler_xlsx_bytes("ipca_bruto.xlsx"))
    s = FakeSession({"https://apisidra.ibge.gov.br": FakeResp(b"<html>manutencao</html>"), m_ipca.URL_FTP: FakeResp(zipado)})
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


def rotas_icti(xlsx=None, pagina1=BLOG, post=POST):
    return {m_icti.BLOG + "/page/": FakeResp(pagina1), m_icti.BLOG: FakeResp(pagina1), URL_POST: FakeResp(post),
            URL_XLSX: FakeResp(xlsx if xlsx is not None else ler_xlsx_bytes("icti_bruto.xlsx"))}


def test_icti_fluxo_completo(dormir):
    s = FakeSession(rotas_icti())
    res = ColetorICTI(INDICES["icti"], s, dormir).coletar(mudo)
    assert len(res.registros) == 164 and res.fonte["arquivo"].endswith("agosto_26.xlsx")
    assert res.registros[-1].competencia == "2026-08" and not res.avisos


def test_icti_procura_nas_proximas_paginas(dormir):
    vazia = "<html><body><article><h1 class='entry-title'><a href='/x'>Outro tema</a></h1></article></body></html>"
    rotas = rotas_icti()
    rotas[m_icti.BLOG] = FakeResp(vazia)
    rotas[m_icti.BLOG + "/page/2/"] = FakeResp(BLOG)
    s = FakeSession(rotas)
    assert len(ColetorICTI(INDICES["icti"], s, dormir).coletar(mudo).registros) == 164


def test_icti_sem_publicacao(dormir):
    vazia = "<html><body><article><h1 class='entry-title'><a href='/x'>Outro tema</a></h1></article></body></html>"
    s = FakeSession({m_icti.BLOG: FakeResp(vazia)})
    with pytest.raises(ErroEstrutura, match="Nenhuma publicação"):
        ColetorICTI(INDICES["icti"], s, dormir).coletar(mudo)
    assert s.n(m_icti.BLOG) == m_icti.MAX_PAGINAS


def test_icti_arquivo_corrompido(dormir):
    s = FakeSession(rotas_icti(xlsx=b"<html>erro</html>"))
    with pytest.raises(ErroEstrutura, match="não é uma planilha"):
        ColetorICTI(INDICES["icti"], s, dormir).coletar(mudo)


def test_icti_timeout_na_blog(dormir):
    s = FakeSession({m_icti.BLOG: requests.exceptions.ConnectTimeout()})
    with pytest.raises(ErroColeta):
        ColetorICTI(INDICES["icti"], s, dormir).coletar(mudo)
