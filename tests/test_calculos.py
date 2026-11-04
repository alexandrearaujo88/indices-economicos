"""Cálculos acumulados: valores oficiais, comparação com o algoritmo original, bordas e erros."""
import json
from decimal import Decimal

import pytest

from conftest import FIX, RAIZ
from indices import armazenamento as arm
from indices.calculos import ErroCalculo, acumulado, parse_mm_aaaa, parse_valor_br


@pytest.fixture(scope="module")
def D(tmp_path_factory):
    from indices.migracao import coletores_legado
    from indices.orquestrador import executar
    base = tmp_path_factory.mktemp("d")
    executar(None, base=base, coletores=coletores_legado(FIX), eco=None)
    return {c: arm.carregar(c, base) for c in ("ipca", "icti", "ist")}


def pct(D, c, i, f, modo="inclusivo", valor=None):
    return acumulado(D[c], i, f, modo, valor).como_dict()


# --- valores oficiais conhecidos (IBGE: IPCA anual) e publicados pelas fontes (12 meses)
@pytest.mark.parametrize("ini,fim,esperado", [("2025-01", "2025-12", "4.26"), ("2024-01", "2024-12", "4.83"),
                                              ("2023-01", "2023-12", "4.62"), ("1994-01", "1994-12", "916.46")])
def test_ipca_anual_oficial(D, ini, fim, esperado):
    assert pct(D, "ipca", ini, fim)["percentual"] == esperado


def test_12_meses_publicados_batem_nos_dois_modos(D):
    # IPCA set/26 = 4,58%; ICTI ago/26 = 3,89% (publicados nas fontes)
    assert pct(D, "ipca", "2025-10", "2026-09")["percentual"] == "4.58"
    assert pct(D, "ipca", "2025-09", "2026-09", "data_base")["percentual"] == "4.58"
    assert pct(D, "icti", "2025-09", "2026-08")["percentual"] == "3.89"
    assert pct(D, "icti", "2025-08", "2026-08", "data_base")["percentual"] == "3.89"


def test_todo_12m_publicado_coincide_com_o_calculado(D):
    for c in ("ipca", "icti"):
        for e in D[c]["serie"]:
            if e["var_12m_origem"] != "fonte":
                continue
            ini = arm_mes(e["competencia"], -11)
            if ini < D[c]["primeira_competencia"]:
                continue
            calc = float(acumulado(D[c], ini, e["competencia"]).percentual)
            assert abs(calc - e["var_12m"]) <= 0.0101, (c, e["competencia"], calc, e["var_12m"])


def arm_mes(c, n):
    from indices.tempo import somar_meses
    return somar_meses(c, n)


# --- compatibilidade com o algoritmo ORIGINAL (composição das variações mensais arredondadas)
def original_composto(doc, ini, fim):
    fator = 1.0
    for e in doc["serie"]:
        if ini <= e["competencia"] <= fim:
            fator *= 1 + round(e["var_mes"], 2) / 100
    return (fator - 1) * 100


@pytest.mark.parametrize("c,ini,fim", [("ipca", "2020-01", "2025-12"), ("ipca", "2003-03", "2024-08"),
                                       ("icti", "2013-01", "2026-08"), ("icti", "2018-06", "2023-02"),
                                       ("ist", "2006-02", "2026-09"), ("ist", "2015-01", "2019-12")])
def test_diferenca_para_o_original_e_so_arredondamento(D, c, ini, fim):
    novo = float(acumulado(D[c], ini, fim).percentual)
    velho = original_composto(D[c], ini, fim)
    assert abs(novo - velho) < 0.05, (novo, velho)       # o original acumulava erro de arredondamento


def test_icti_serie_completa_mostra_o_erro_do_original(D):
    novo = float(acumulado(D["icti"], "2013-01", "2026-08").percentual)
    assert novo == pytest.approx(109.30179669576732, abs=1e-9)          # I_final/100 − 1, exato
    assert abs(original_composto(D["icti"], "2013-01", "2026-08") - novo) > 0.0005   # original difere


# --- modos
def test_modos_diferem_pelo_mes_inicial(D):
    ipca = {e["competencia"]: e for e in D["ipca"]["serie"]}
    r_incl = acumulado(D["ipca"], "2024-01", "2024-12", "inclusivo")
    r_base = acumulado(D["ipca"], "2024-01", "2024-12", "data_base")
    assert r_incl.meses == 12 and r_base.meses == 11
    assert float(r_incl.fator) == pytest.approx(ipca["2024-12"]["indice"] / ipca["2023-12"]["indice"])
    assert float(r_base.fator) == pytest.approx(ipca["2024-12"]["indice"] / ipca["2024-01"]["indice"])


def test_um_mes_equivale_a_variacao_mensal_publicada(D):
    assert pct(D, "ipca", "2026-09", "2026-09")["percentual"] == "0.82"
    assert pct(D, "ist", "2026-08", "2026-08")["percentual"] == "-0.37"
    assert pct(D, "ist", "2023-07", "2023-07")["percentual"] == "-0.22"   # linha com "- 0,22%" no original


# --- valor corrigido
def test_valor_corrigido_com_arredondamento_comercial(D):
    r = pct(D, "ipca", "2025-01", "2025-12", valor="1.000,00")
    assert r["valor_corrigido"] == "1042.64" and r["fator"].startswith("1.0426")
    r2 = acumulado(D["ipca"], "2025-01", "2025-12", valor="1000")
    assert r2.valor_corrigido == Decimal("1000") * r2.fator


def test_valor_nao_corrigido_antes_do_real(D):
    r = acumulado(D["ipca"], "1994-01", "1994-12", valor="1000")
    assert r.valor_corrigido is None and any("jul/1994" in a for a in r.avisos) and str(r.percentual.quantize(Decimal("0.01"))) == "916.46"
    ok = acumulado(D["ipca"], "1994-07", "1995-07", valor="1000")
    assert ok.valor_corrigido is not None


def test_ist_primeiro_mes_usa_indice_reconstruido_com_aviso(D):
    r = acumulado(D["ist"], "2006-01", "2006-03")
    assert any("reconstruído" in a for a in r.avisos)
    assert float(r.percentual) == pytest.approx(((117.299 / (116.969 / 1.0058)) - 1) * 100, abs=1e-6)


# --- erros que o original deixava passar (resultado enganoso)
def test_periodo_invertido_e_erro_explicito(D):
    with pytest.raises(ErroCalculo) as e:
        acumulado(D["ipca"], "2025-12", "2025-01")
    assert e.value.codigo == "periodo_invertido"       # o original devolvia "0,00%"


@pytest.mark.parametrize("c,ini,fim,codigo", [("icti", "2026-01", "2026-09", "nao_divulgado"),
                                              ("ist", "2005-01", "2006-02", "fora_da_serie"),
                                              ("icti", "2012-06", "2013-02", "fora_da_serie")])
def test_periodo_fora_da_serie(D, c, ini, fim, codigo):
    with pytest.raises(ErroCalculo) as e:
        acumulado(D[c], ini, fim)
    assert e.value.codigo == codigo


def test_data_base_exige_datas_diferentes(D):
    with pytest.raises(ErroCalculo):
        acumulado(D["ipca"], "2025-01", "2025-01", "data_base")


def test_modo_invalido(D):
    with pytest.raises(ErroCalculo):
        acumulado(D["ipca"], "2025-01", "2025-02", "xyz")


@pytest.mark.parametrize("t,esperado", [("03/2024", "2024-03"), ("3/2024", "2024-03"), ("12/1999", "1999-12"), ("2024-03", "2024-03")])
def test_parse_datas(t, esperado):
    assert parse_mm_aaaa(t) == esperado


@pytest.mark.parametrize("t", ["13/2024", "00/2024", "03/24", "março", "", "2024-13"])
def test_parse_datas_invalidas(t):
    with pytest.raises(ErroCalculo):
        parse_mm_aaaa(t)


@pytest.mark.parametrize("t,esperado", [("1.000,50", "1000.50"), ("1000", "1000"), ("R$ 2.345,67", "2345.67"), ("0,99", "0.99")])
def test_parse_valor(t, esperado):
    assert parse_valor_br(t) == Decimal(esperado)


@pytest.mark.parametrize("t", ["abc", "1,2,3", "-5", ""])
def test_parse_valor_invalido(t):
    with pytest.raises(ErroCalculo):
        parse_valor_br(t)


# --- vetores compartilhados com o JavaScript (paridade Python × JS)
def test_gera_vetores_para_paridade_com_javascript(D):
    casos = []
    for c, ini, fim, modo, valor in [
        ("ipca", "2025-01", "2025-12", "inclusivo", "1000"), ("ipca", "2020-03", "2026-09", "inclusivo", "12345.67"),
        ("ipca", "2025-09", "2026-09", "data_base", "1000"), ("ipca", "1994-01", "1994-12", "inclusivo", "1000"),
        ("ipca", "1994-07", "2000-06", "inclusivo", "99.99"), ("icti", "2013-01", "2026-08", "inclusivo", "1000"),
        ("icti", "2024-05", "2025-05", "data_base", "250000"), ("ist", "2006-01", "2006-06", "inclusivo", "1000"),
        ("ist", "2014-06", "2017-05", "inclusivo", "1000"), ("ist", "2023-07", "2023-07", "inclusivo", "100"),
        ("ist", "2020-01", "2026-09", "data_base", None)]:
        casos.append({"indice": c, "inicio": ini, "fim": fim, "modo": modo, "valor": valor,
                      "esperado": acumulado(D[c], ini, fim, modo, valor).como_dict()})
    erros = [{"indice": "ipca", "inicio": "2025-12", "fim": "2025-01", "modo": "inclusivo", "codigo": "periodo_invertido"},
             {"indice": "icti", "inicio": "2026-01", "fim": "2026-09", "modo": "inclusivo", "codigo": "nao_divulgado"},
             {"indice": "ist", "inicio": "2005-01", "fim": "2006-02", "modo": "inclusivo", "codigo": "fora_da_serie"}]
    (RAIZ / "tests" / "vetores_calculo.json").write_text(json.dumps({"casos": casos, "erros": erros}, ensure_ascii=False, indent=1))
    assert len(casos) == 11
