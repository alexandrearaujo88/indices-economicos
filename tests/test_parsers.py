import json

import pytest

from conftest import FIX, html_ist, ler_xlsx_bytes
from indices.coletores import icti, ipca, ist
from indices.modelos import ErroEstrutura, num_br


# ------------------------------------------------------------------ números
@pytest.mark.parametrize("txt,esperado", [
    ("0,61%", 0.61), ("- 0,22%", -0.22), ("–0,05%", -0.05), ("1.234,567", 1234.567),
    ("362,701", 362.701), (" 4.58 ", 4.58), ("-", None), ("", None), (None, None), (3, 3.0)])
def test_num_br(txt, esperado):
    assert num_br(txt) == esperado


def test_num_br_rejeita_texto():
    with pytest.raises(ValueError):
        num_br("abc")


# ------------------------------------------------------------------ IPCA
def sidra_json(regs):
    cab = {"NC": "Nível Territorial (Código)", "V": "Valor", "D2C": "Variável (Código)", "D3C": "Mês (Código)"}
    out = [cab]
    for comp, (i, m, a) in regs.items():
        per = comp.replace("-", "")
        for cod, val, nome in (("2266", i, "IPCA - Número-índice (base: dezembro de 1993 = 100)"),
                               ("63", m, "IPCA - Variação mensal"), ("2265", a, "IPCA - Variação acumulada em 12 meses")):
            out.append({"V": "..." if val is None else f"{val:.4f}", "D2C": cod, "D2N": nome, "D3C": per})
    return out


def test_parse_sidra_ok_e_filtra_inicio():
    dados = sidra_json({"1993-12": (100.0, 2.0, None), "1994-01": (141.31, 41.31, 2693.84),
                        "1994-02": (198.22, 40.27, 3035.71)})
    regs, base = ipca.parse_sidra(dados, "1994-01")
    assert [r.competencia for r in regs] == ["1994-01", "1994-02"]
    assert regs[0].indice == 141.31 and regs[0].var_mes == 41.31 and regs[0].var_12m == 2693.84
    assert base == "dez/1993 = 100"


def test_parse_sidra_ignora_competencia_sem_indice():
    dados = sidra_json({"2026-09": (7695.82, 0.82, 4.58), "2026-10": (None, None, None)})
    regs, _ = ipca.parse_sidra(dados)
    assert [r.competencia for r in regs] == ["2026-09"]


@pytest.mark.parametrize("ruim", [{}, [], [{"x": 1}], [{"V": 1, "D2C": 1, "D3C": 1}, {"D2C": "999", "D3C": "202601", "V": "1"}]])
def test_parse_sidra_estrutura_inesperada(ruim):
    with pytest.raises(ErroEstrutura):
        ipca.parse_sidra(ruim)


def test_parse_planilha_ipca_original():
    linhas = ipca._linhas_xlsx(ler_xlsx_bytes("ipca_bruto.xlsx"))
    regs, base = ipca.parse_linhas_ipca(linhas, "1994-01")
    assert len(regs) == 393 and regs[0].competencia == "1994-01" and regs[-1].competencia == "2026-09"
    assert regs[-1].indice == 7695.82 and regs[-1].var_mes == 0.82 and regs[-1].var_12m == 4.58
    assert base == "dez/1993 = 100"


def test_parse_planilha_ipca_sem_cabecalho():
    with pytest.raises(ErroEstrutura):
        ipca.parse_linhas_ipca([["a", "b"], ["c", "d"]])


def test_parse_zip_ftp():
    from conftest import zip_com
    regs, base, nome = ipca.parse_zip_ftp(zip_com("ipca_202609SerieHist.xlsx", ler_xlsx_bytes("ipca_bruto.xlsx")), "1994-01")
    assert len(regs) == 393 and nome.endswith(".xlsx")


# ------------------------------------------------------------------ IST
def test_parse_ist_variacoes_de_formato():
    html = html_ist([("Set/26", "0,61%", "362,701"), ("jun/14", "0,29%", "170,255"), ("Maio/17", "0,45%", "207,006"),
                     ("Jul/23", "- 0,22%", "317,815"), ("Fev/16*", "0,63%", "196,995"), ("Jan/2006", "0,58%", "116,969")])
    regs, avisos = ist.parse_html(html)
    por = {r.competencia: r for r in regs}
    assert set(por) == {"2026-09", "2014-06", "2017-05", "2023-07", "2016-02", "2006-01"}
    assert por["2023-07"].var_mes == -0.22 and por["2014-06"].indice == 170.255
    assert not avisos


def test_parse_ist_ignora_notas_e_relata_linhas_invalidas():
    html = html_ist([("Set/26", "0,61%", "362,701"), ("Xyz/26", "0,10%", "1"), ("*Valor corrigido em março de 2016", "", "")])
    html = html.replace("</table>", "<tr><td>Nota de rodapé</td></tr></table>")
    regs, avisos = ist.parse_html(html)
    assert len(regs) == 1 and any("ignoradas" in a for a in avisos)


@pytest.mark.parametrize("classe,cab", [("listing", ("Referência", "Variação", "IST")), ("x", ("Referência", "Variação", "IST")), ("slate-table-block", ("a", "b", "c"))])
def test_parse_ist_detecta_tabela_por_classe_ou_cabecalho(classe, cab):
    regs, _ = ist.parse_html(html_ist([("Set/26", "0,61%", "362,701")], classe=classe, cabecalho=cab))
    assert len(regs) == 1


def test_parse_ist_sem_tabela_relata_titulo():
    with pytest.raises(ErroEstrutura) as e:
        ist.parse_html("<html><head><title>Acesso negado</title></head><body>bloqueado</body></html>")
    assert "Acesso negado" in str(e.value)


def test_parse_ist_original_completo():
    import openpyxl
    ws = openpyxl.load_workbook(FIX / "ist_bruto.xlsx", read_only=True).worksheets[0]
    linhas = [(r[0], r[1], r[2]) for r in list(ws.iter_rows(values_only=True))[1:]]
    regs, _ = ist.parse_html(html_ist(linhas))
    assert len(regs) == 249                      # o original descartava 2 (jun/14 e Maio/17)
    assert {"2014-06", "2017-05"} <= {r.competencia for r in regs}


# ------------------------------------------------------------------ ICTI
def test_parse_icti_atual_e_antigo():
    regs, base = icti.parse_xlsx(ler_xlsx_bytes("icti_bruto.xlsx"), "2013-01")
    assert len(regs) == 164 and regs[0].competencia == "2013-01" and regs[-1].competencia == "2026-08"
    assert base == "dez/2012 = 100"
    assert regs[0].var_12m is None and regs[-1].var_12m == pytest.approx(3.8936341198)
    antigo, _ = icti.parse_xlsx(ler_xlsx_bytes("icti_bruto_antigo.xlsx"))
    assert len(antigo) == 154


def test_parse_icti_rejeita_arquivo_que_nao_e_xlsx():
    with pytest.raises(ErroEstrutura):
        icti.parse_xlsx(b"<html>erro 500</html>")


def test_parse_icti_cabecalho_ausente():
    import io, openpyxl
    wb = openpyxl.Workbook(); wb.active.append(["x", "y"]); buf = io.BytesIO(); wb.save(buf)
    with pytest.raises(ErroEstrutura):
        icti.parse_xlsx(buf.getvalue())


BLOG = """<html><body>
<article><h1 class="entry-title"><a href="/cartadeconjuntura/index.php/2026/10/pib-variacao-maio/">PIB: variação percentual em maio de 2026</a></h1></article>
<article><h1 class="entry-title"><a href="/cartadeconjuntura/index.php/2026/10/icti-agosto/">Índice de Custo da Tecnologia da Informação (ICTI) – agosto de 2026</a></h1></article>
<article><h1 class="entry-title"><a href="/cartadeconjuntura/index.php/2026/09/icti-julho/">Indice de Custo da Tecnologia da Informação (ICTI) – julho de 2026</a></h1></article>
</body></html>"""


def test_descoberta_ignora_posts_de_outros_temas_e_acentos():
    posts = icti.achar_posts(BLOG, icti.BLOG)
    assert [p["competencia"] for p in posts] == ["2026-08", "2026-07"]
    assert posts[0]["url"].startswith("https://www.ipea.gov.br/cartadeconjuntura/index.php/2026/10/")


def test_link_da_planilha_prefere_icti_xlsx():
    html = """<div class="entry-content"><a href="x/texto.pdf">Acesse o texto completo</a>
      <a href="https://www.ipea.gov.br/wp/261015_ICTI_serie_completa_agosto_26.xlsx">Dados Xls</a>
      <a href="https://outro.gov.br/outra.xlsx">Outra</a><img src="t1.png"></div>"""
    assert icti.achar_link_planilha(html, "https://www.ipea.gov.br/post/").endswith("261015_ICTI_serie_completa_agosto_26.xlsx")


def test_link_da_planilha_ausente():
    with pytest.raises(ErroEstrutura):
        icti.achar_link_planilha("<div class='entry-content'><a href='a.pdf'>x</a></div>", "https://x/")
