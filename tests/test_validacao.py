from datetime import datetime

import pytest

from indices import armazenamento as arm
from indices.config import INDICES, TZ
from indices.modelos import Registro
from indices.validacao import validar
from test_armazenamento import regs_basicos

AGORA = datetime(2026, 11, 4, 9, 0, tzinfo=TZ)
CFG = INDICES["ist"]


def serie(n=210, inicio=(2006, 1)):
    return regs_basicos(n, inicio)


def doc_de(regs):
    return arm.mesclar(None, regs, CFG, AGORA)[0]


def ok(regs, doc=None, base=None, cfg=CFG):
    return validar(regs, doc, cfg, base, AGORA)


def test_serie_boa_passa_sem_avisos():
    v = ok(serie())
    assert v.ok and v.avisos == []


def test_poucos_registros_bloqueia():
    assert "Apenas 12" in ok(serie(12)).erros[0]


def test_lacuna_bloqueia():
    r = serie()
    del r[100]
    assert any("Lacuna" in e for e in ok(r).erros)


def test_duplicata_identica_e_descartada_e_conflitante_bloqueia():
    r = serie()
    v = ok(r + [r[5]])
    assert v.ok and any("duplicada" in a for a in v.avisos)
    r2 = r + [Registro(r[5].competencia, r[5].indice * 2, r[5].var_mes)]
    assert any("duas vezes" in e for e in ok(r2).erros)


def test_valor_implausivel_bloqueia():
    r = serie()
    r[50] = Registro(r[50].competencia, r[50].indice, 99.0)
    assert any("implausível" in e for e in ok(r).erros)
    r[50] = Registro(r[50].competencia, -1.0, 0.5)
    assert any("inválido" in e for e in ok(r).erros)


def test_variacao_incompativel_com_indices_bloqueia():
    r = serie()
    r[60] = Registro(r[60].competencia, r[60].indice, r[60].var_mes + 1.0)
    assert any("incompatível" in e for e in ok(r).erros)


def test_mudanca_de_base_bloqueia():
    cfg = INDICES["ipca"]
    r = regs_basicos(390, (1994, 1))
    assert validar(r, None, cfg, "dez/1993 = 100", AGORA).ok
    assert any("Base do índice mudou" in e for e in validar(r, None, cfg, "dez/2018 = 100", AGORA).erros)


def test_competencia_futura_bloqueia():
    fut = regs_basicos(250 + 8, (2006, 1))          # termina em 2027-06, depois de "agora" (nov/2026)
    assert any("futura" in e for e in ok(fut).erros)


def test_fonte_regredindo_ou_perdendo_dados_bloqueia():
    doc = doc_de(serie(260))
    v = ok(serie(225), doc)
    assert any("regrediu" in e for e in v.erros) and any("perda" in e for e in v.erros)


def test_revisao_pequena_gera_aviso_e_grande_bloqueia():
    base_regs = serie(220)
    doc = doc_de(base_regs)
    pequena = list(base_regs)
    pequena[10] = Registro(pequena[10].competencia, pequena[10].indice * 1.0001, pequena[10].var_mes)
    v = ok(pequena, doc)
    assert v.ok and any("Revisão de valores" in a for a in v.avisos)
    grande = list(base_regs)
    grande[10] = Registro(grande[10].competencia, grande[10].indice * 1.02, grande[10].var_mes)
    assert any("Revisão grande" in e for e in ok(grande, doc).erros)


def test_revisao_em_massa_bloqueia():
    base_regs = serie(220)
    doc = doc_de(base_regs)
    massa = [Registro(r.competencia, r.indice * 1.0001, r.var_mes) for r in base_regs]
    assert any("mudaram de valor de uma vez" in e for e in ok(massa, doc).erros)


def test_muitos_novos_gera_aviso():
    base_regs = serie(215)
    doc = doc_de(base_regs)
    v = ok(serie(220), doc)
    assert v.ok and any("competências novas" in a for a in v.avisos)
