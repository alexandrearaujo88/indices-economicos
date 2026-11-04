import copy
import json
import os
from datetime import datetime

import pytest

from conftest import FIX
from indices import armazenamento as arm
from indices.config import INDICES, TZ
from indices.modelos import Registro

T1 = datetime(2026, 11, 4, 9, 0, tzinfo=TZ)
T2 = datetime(2026, 11, 4, 19, 0, tzinfo=TZ)


def regs_basicos(n=14, inicio=(2025, 1)):
    out, idx, a, m = [], 100.0, *inicio
    for _ in range(n):
        idx *= 1.005
        out.append(Registro(f"{a:04d}-{m:02d}", round(idx, 3), 0.5, None))
        m += 1
        if m > 12:
            a, m = a + 1, 1
    return out


def test_primeira_carga_cria_documento_completo():
    doc, res = arm.mesclar(None, regs_basicos(), INDICES["ist"], T1)
    assert doc["total_registros"] == 14 and doc["primeira_competencia"] == "2025-01"
    assert doc["atualizado_em"] == T1.isoformat(timespec="seconds") and res["alterado"]
    assert doc["serie"][12]["var_12m_origem"] == "calculada" and doc["serie"][0]["var_12m"] is None
    assert doc["serie"][12]["var_12m"] == pytest.approx((doc["serie"][12]["indice"] / doc["serie"][0]["indice"] - 1) * 100)


def test_repetir_a_mesma_coleta_nao_duplica_nem_altera():
    doc1, _ = arm.mesclar(None, regs_basicos(), INDICES["ist"], T1)
    doc2, res = arm.mesclar(doc1, regs_basicos(), INDICES["ist"], T2)
    assert not res["alterado"] and res["novos"] == [] and res["revisoes"] == []
    assert doc2 == doc1                                   # inclusive atualizado_em
    assert len({e["competencia"] for e in doc2["serie"]}) == len(doc2["serie"]) == 14
    assert arm.serializar(doc2) == arm.serializar(doc1)


def test_novos_meses_sao_acrescentados_sem_tocar_nos_antigos():
    doc1, _ = arm.mesclar(None, regs_basicos(14), INDICES["ist"], T1)
    doc2, res = arm.mesclar(doc1, regs_basicos(15), INDICES["ist"], T2)
    assert res["novos"] == ["2026-03"] and doc2["ultima_competencia"] == "2026-03"
    assert doc2["serie"][:14] == doc1["serie"] and doc2["atualizado_em"] == T2.isoformat(timespec="seconds")
    assert doc2["serie"][14]["primeira_coleta"] == T2.isoformat(timespec="seconds")


def test_revisao_preserva_historico_do_valor():
    doc1, _ = arm.mesclar(None, regs_basicos(), INDICES["ist"], T1)
    regs = regs_basicos()
    antigo = regs[3].indice
    regs[3] = Registro(regs[3].competencia, antigo + 0.5, 0.5, None)
    doc2, res = arm.mesclar(doc1, regs, INDICES["ist"], T2)
    e = next(x for x in doc2["serie"] if x["competencia"] == regs[3].competencia)
    assert e["indice"] == antigo + 0.5
    assert e["revisoes"] == [{"em": T2.isoformat(timespec="seconds"), "campo": "indice", "de": antigo, "para": antigo + 0.5}]
    assert res["revisoes"][0]["competencia"] == regs[3].competencia and res["alterado"]


def test_competencia_ausente_na_fonte_e_mantida():
    doc1, _ = arm.mesclar(None, regs_basicos(14), INDICES["ist"], T1)
    doc2, res = arm.mesclar(doc1, regs_basicos(14)[2:], INDICES["ist"], T2)
    assert res["ausentes"] == ["2025-01", "2025-02"] and doc2["total_registros"] == 14


def test_fonte_sem_variacao_nao_apaga_valor_armazenado():
    doc1, _ = arm.mesclar(None, regs_basicos(), INDICES["ist"], T1)
    regs = [Registro(r.competencia, r.indice, None, None) for r in regs_basicos()]
    doc2, res = arm.mesclar(doc1, regs, INDICES["ist"], T2)
    assert doc2 == doc1 and not res["alterado"]


def test_var_12m_publicada_pela_fonte_prevalece_e_nao_e_recalculada():
    regs = regs_basicos()
    regs[-1] = Registro(regs[-1].competencia, regs[-1].indice, 0.5, 9.99)
    doc, _ = arm.mesclar(None, regs, INDICES["ipca"], T1)
    assert doc["serie"][-1]["var_12m"] == 9.99 and doc["serie"][-1]["var_12m_origem"] == "fonte"


def test_gravacao_atomica_nao_corrompe_arquivo_se_falhar(base, monkeypatch):
    doc, _ = arm.mesclar(None, regs_basicos(), INDICES["ist"], T1)
    arm.salvar("ist", doc, base)
    original = (base / "ist.json").read_text()
    doc2, _ = arm.mesclar(doc, regs_basicos(15), INDICES["ist"], T2)

    def quebra(*a, **k):
        raise OSError("disco cheio")
    monkeypatch.setattr(os, "replace", quebra)
    with pytest.raises(OSError):
        arm.salvar("ist", doc2, base)
    monkeypatch.undo()
    assert (base / "ist.json").read_text() == original
    assert list(base.glob("*.tmp")) == []


def test_json_gravado_e_valido_e_recarregavel(base):
    doc, _ = arm.mesclar(None, regs_basicos(), INDICES["ist"], T1)
    arm.salvar("ist", doc, base)
    assert arm.carregar("ist", base) == doc
    json.loads((base / "ist.json").read_text(encoding="utf-8"))
    assert arm.carregar("inexistente", base) is None
