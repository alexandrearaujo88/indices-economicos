"""Execução orquestrada: isolamento de falhas, preservação de dados, trava, logs e status."""
import json
import os
from pathlib import Path

import pytest

from conftest import FIX
from indices import armazenamento as arm
from indices import logs as lg
from indices.migracao import coletores_legado
from indices.modelos import ErroColeta, Registro, ResultadoColeta
from indices.orquestrador import executar, trava, TravaOcupada


class Fixo:
    def __init__(self, res=None, erro=None):
        self.res, self.erro = res, erro

    def coletar(self, log):
        log("coletor de teste")
        if self.erro:
            raise self.erro
        return self.res


def legado():
    return coletores_legado(FIX)


def test_semeadura_completa(docs_semeados):
    base = docs_semeados
    for c, n in (("ipca", 393), ("icti", 164), ("ist", 249)):
        assert arm.carregar(c, base)["total_registros"] == n
    st = json.loads((base / "status.json").read_text())
    assert st["fuso"] == "America/Maceio" and st["agendamento"]["horarios"] == ["09:00", "19:00"]
    assert set(st["indices"]) == {"ipca", "icti", "ist"} and st["execucao"]["resultado"] == "sucesso"


def test_falha_em_um_indice_nao_afeta_os_demais_e_preserva_dados(docs_semeados, relogio):
    base = docs_semeados
    antes = {c: (base / f"{c}.json").read_bytes() for c in ("ipca", "icti", "ist")}
    cols = legado()
    cols["icti"] = Fixo(erro=ErroColeta("tempo esgotado"))
    cols["ist"] = Fixo(erro=RuntimeError("bug inesperado no parser"))
    res = executar(None, base=base, coletores=cols, relogio=relogio, eco=None, gatilho="agendado")
    por = {e["indice"]: e for e in res["entradas"]}
    assert por["ipca"]["resultado"] == "sucesso"
    assert por["icti"]["resultado"] == "erro" and por["ist"]["resultado"] == "erro"
    assert "bug inesperado" in por["ist"]["mensagem"]
    assert {c: (base / f"{c}.json").read_bytes() for c in antes} == antes     # nada foi alterado
    st = json.loads((base / "status.json").read_text())
    assert st["indices"]["icti"]["resultado"] == "erro" and st["indices"]["icti"]["ultima_falha"]["mensagem"]
    assert st["indices"]["ipca"]["resultado"] == "sucesso" and st["execucao"]["resultado"] == "aviso"


def test_dados_invalidos_nao_sobrescrevem(docs_semeados, relogio):
    base = docs_semeados
    antes = (base / "ist.json").read_bytes()
    pouco = ResultadoColeta([Registro("2026-09", 362.701, 0.61)], {}, None, [])
    res = executar(["ist"], base=base, coletores={"ist": Fixo(pouco)}, relogio=relogio, eco=None)
    e = res["entradas"][0]
    assert e["resultado"] == "erro" and "Validação reprovou" in e["mensagem"] and e["resumo"]["gravado"] is False
    assert (base / "ist.json").read_bytes() == antes


def test_execucao_repetida_e_idempotente(docs_semeados, relogio):
    base = docs_semeados
    antes = {c: (base / f"{c}.json").read_bytes() for c in ("ipca", "icti", "ist")}
    res = executar(None, base=base, coletores=legado(), relogio=relogio, eco=None)
    assert all(e["resultado"] == "sucesso" and "Sem novidades" in e["mensagem"] for e in res["entradas"])
    assert {c: (base / f"{c}.json").read_bytes() for c in antes} == antes


def test_nova_competencia_e_revisao_sao_registradas(docs_semeados, relogio):
    base = docs_semeados
    doc = arm.carregar("ist", base)
    regs = [Registro(e["competencia"], e["indice"], e["var_mes"]) for e in doc["serie"]]
    regs[-5] = Registro(regs[-5].competencia, regs[-5].indice + 0.001, regs[-5].var_mes)
    novo = Registro("2026-10", round(regs[-1].indice * 1.004, 3), 0.40)
    res = executar(["ist"], base=base, coletores={"ist": Fixo(ResultadoColeta(regs + [novo], {}, None, []))},
                   relogio=relogio, eco=None)
    e = res["entradas"][0]
    assert e["resultado"] == "aviso" and e["resumo"]["novos"] == ["2026-10"] and len(e["resumo"]["revisoes"]) == 1
    novo_doc = arm.carregar("ist", base)
    assert novo_doc["ultima_competencia"] == "2026-10" and novo_doc["serie"][-6]["revisoes"]


def test_dry_run_nao_grava_nada(docs_semeados, relogio):
    base = docs_semeados
    snap = {p: p.read_bytes() for p in base.rglob("*") if p.is_file()}
    res = executar(None, base=base, coletores=legado(), relogio=relogio, eco=None, dry_run=True)
    assert len(res["entradas"]) == 3 and all(e["operacao"].endswith("(simulação)") for e in res["entradas"])
    assert {p: p.read_bytes() for p in base.rglob("*") if p.is_file()} == snap


def test_trava_impede_execucao_concorrente(docs_semeados, relogio):
    base = docs_semeados
    with trava(base):
        res = executar(None, base=base, coletores=legado(), relogio=relogio, eco=None)
    assert res["entradas"][0]["indice"] == "sistema" and res["entradas"][0]["resultado"] == "erro"
    assert "trava" in res["entradas"][0]["mensagem"].lower()
    assert not (base / ".atualizacao.lock").exists()
    assert lg.ler_arquivo("sistema", base)[0]["resultado"] == "erro"


def test_trava_abandonada_e_descartada(base):
    p = base / ".atualizacao.lock"
    p.write_text("pid 1")
    os.utime(p, (1, 1))
    with trava(base):
        assert p.exists()
    assert not p.exists()


def test_trava_liberada_mesmo_com_excecao(base):
    with pytest.raises(RuntimeError):
        with trava(base):
            raise RuntimeError("x")
    assert not (base / ".atualizacao.lock").exists()


def test_execucao_de_reserva_e_ignorada_se_houve_execucao_recente(docs_semeados, relogio):
    base = docs_semeados
    n_antes = len(lg.ler_arquivo("ipca", base))
    # a execução da semeadura terminou há segundos (relógio do teste avança 1 s por leitura)
    res = executar(None, base=base, coletores=legado(), relogio=relogio, eco=None, gatilho="reserva",
                   pular_se_recente_min=120)
    assert res["pulada"] and len(lg.ler_arquivo("ipca", base)) == n_antes
    relogio.estado["t"] = relogio.estado["t"].replace(hour=22)      # 13 h depois: executa
    res2 = executar(None, base=base, coletores=legado(), relogio=relogio, eco=None, gatilho="reserva",
                    pular_se_recente_min=120)
    assert not res2["pulada"] and len(lg.ler_arquivo("ipca", base)) == n_antes + 1


def test_so_indices_pedidos_sao_executados(docs_semeados, relogio):
    res = executar(["icti"], base=docs_semeados, coletores=legado(), relogio=relogio, eco=None)
    assert [e["indice"] for e in res["entradas"]] == ["icti"]
    st = json.loads((docs_semeados / "status.json").read_text())
    assert set(st["indices"]) == {"ipca", "icti", "ist"}            # status dos demais é preservado


# ------------------------------------------------------------------ logs
def test_logs_ordem_decrescente_e_consolidado(docs_semeados, relogio):
    base = docs_semeados
    for gat in ("agendado", "manual", "agendado"):
        executar(None, base=base, coletores=legado(), relogio=relogio, eco=None, gatilho=gat)
        relogio.estado["t"] = relogio.estado["t"].replace(hour=(relogio.estado["t"].hour + 1) % 24 or 1)
    for cod in ("ipca", "icti", "ist"):
        regs = json.loads((base / "logs" / f"{cod}.json").read_text())["registros"]
        inicios = [r["inicio"] for r in regs]
        assert len(regs) == 4 and inicios == sorted(inicios, reverse=True)
        assert all(r["etapas"] for r in regs)
    cons = json.loads((base / "logs" / "consolidado.json").read_text())["registros"]
    inicios = [r["inicio"] for r in cons]
    assert len(cons) == 12 and inicios == sorted(inicios, reverse=True)
    assert {"indice", "operacao", "resultado", "mensagem", "inicio", "duracao_ms"} <= set(cons[0])
    assert "etapas" not in cons[0]


def test_log_sobrevive_a_linha_truncada(docs_semeados):
    base = docs_semeados
    arq = next((base / "logs" / "ipca").glob("*.jsonl"))
    with arq.open("a") as f:
        f.write('{"id":"quebrado","inicio":"2026-')          # queda no meio da escrita
    assert len(lg.ler_arquivo("ipca", base)) == 1


def test_log_nao_expoe_dados_sensiveis():
    s = lg.sanitizar("falha em /home/runner/work/x/y.py com token=abc123 de 10.1.2.3 url https://a/b?chave=segredo")
    assert "/home" not in s and "abc123" not in s and "10.1.2.3" not in s and "segredo" not in s
    assert "stack trace" in lg.sanitizar("Traceback (most recent call last):\n  File x\nValueError")
    assert len(lg.sanitizar("x" * 1000)) <= 400


# ------------------------------------------------------------------ situação por índice (painel)
def _status(base):
    return json.loads((base / "status.json").read_text())["indices"]


def test_situacao_carga_inicial_sem_novidades_e_alteracoes(docs_semeados, relogio):
    base = docs_semeados
    st = _status(base)
    assert all(st[c]["situacao"] == "atualizado" and st[c]["ultima_alteracao_tipo"] == "carga_inicial" for c in st)
    alterado_em = st["ist"]["ultima_alteracao_dados"]

    executar(None, base=base, coletores=legado(), relogio=relogio, eco=None, gatilho="agendado")
    st = _status(base)
    assert all(st[c]["situacao"] == "sem_novidades" and st[c]["resultado"] == "sucesso" for c in st)
    assert st["ist"]["ultima_alteracao_dados"] == alterado_em and st["ist"]["ultima_alteracao_tipo"] == "carga_inicial"
    assert st["ist"]["ultima_coleta"] == {"novas_competencias": [], "competencias_revisadas": []}

    doc = arm.carregar("ist", base)
    regs = [Registro(e["competencia"], e["indice"], e["var_mes"]) for e in doc["serie"]]
    novo = Registro("2026-10", round(regs[-1].indice * 1.004, 3), 0.40)
    executar(["ist"], base=base, coletores={"ist": Fixo(ResultadoColeta(regs + [novo], {}, None, []))},
             relogio=relogio, eco=None)
    st = _status(base)["ist"]
    assert st["situacao"] == "atualizado" and st["ultima_alteracao_tipo"] == "novas_competencias"
    assert st["ultima_coleta"]["novas_competencias"] == ["2026-10"] and st["ultima_alteracao_dados"] != alterado_em

    regs2 = regs + [novo]
    regs2[-4] = Registro(regs2[-4].competencia, regs2[-4].indice + 0.002, regs2[-4].var_mes)
    executar(["ist"], base=base, coletores={"ist": Fixo(ResultadoColeta(regs2, {}, None, []))}, relogio=relogio, eco=None)
    st = _status(base)["ist"]
    assert st["situacao"] == "atualizado" and st["ultima_alteracao_tipo"] == "revisao"
    assert st["ultima_coleta"]["competencias_revisadas"] == [regs2[-4].competencia]


def test_falha_nao_apaga_ultima_alteracao_nem_ultima_coleta_ok(docs_semeados, relogio):
    base = docs_semeados
    antes = _status(base)["icti"]
    executar(["icti"], base=base, coletores={"icti": Fixo(erro=ErroColeta("fonte fora do ar"))},
             relogio=relogio, eco=None)
    st = _status(base)["icti"]
    assert st["situacao"] == "falha" and st["resultado"] == "erro"
    assert st["ultima_coleta_ok"] == antes["ultima_coleta_ok"]
    assert st["ultima_alteracao_dados"] == antes["ultima_alteracao_dados"] and st["ultima_alteracao_tipo"] == "carga_inicial"


def test_tipo_de_alteracao_e_inferido_do_proprio_arquivo(docs_semeados):
    from indices.orquestrador import tipo_alteracao_doc
    doc = arm.carregar("ipca", docs_semeados)
    assert tipo_alteracao_doc(doc) == "carga_inicial"
    doc["serie"][-1]["primeira_coleta"] = "2026-12-01T09:00:00-03:00"
    assert tipo_alteracao_doc(doc) is None and tipo_alteracao_doc(None) is None


# ------------------------------------------------------------------ reserva: nova tentativa só do que falhou
def test_reserva_repete_apenas_indices_que_falharam_e_depois_ignora(docs_semeados, relogio):
    base = docs_semeados
    cols = legado()
    cols["icti"] = Fixo(erro=ErroColeta("tempo esgotado"))
    executar(None, base=base, coletores=cols, relogio=relogio, eco=None, gatilho="agendado")   # 1ª do dia
    assert _status(base)["icti"]["situacao"] == "falha"
    res = executar(None, base=base, coletores=legado(), relogio=relogio, eco=None, gatilho="reserva",
                   pular_se_recente_min=120)
    assert not res["pulada"] and [e["indice"] for e in res["entradas"]] == ["icti"]
    st = _status(base)
    assert st["icti"]["situacao"] == "sem_novidades" and st["ipca"]["situacao"] == "sem_novidades"
    n = len(lg.ler_arquivo("icti", base))
    res2 = executar(None, base=base, coletores=legado(), relogio=relogio, eco=None, gatilho="reserva",
                    pular_se_recente_min=120)
    assert res2["pulada"] and len(lg.ler_arquivo("icti", base)) == n


def test_agendada_atrasada_depois_da_reserva_nao_duplica_a_coleta(docs_semeados, relogio):
    base = docs_semeados
    executar(None, base=base, coletores=legado(), relogio=relogio, eco=None, gatilho="reserva", pular_se_recente_min=120)
    n = len(lg.ler_arquivo("ipca", base))
    res = executar(None, base=base, coletores=legado(), relogio=relogio, eco=None, gatilho="agendado",
                   pular_se_recente_min=120)
    assert res["pulada"] and len(lg.ler_arquivo("ipca", base)) == n


# ------------------------------------------------------------------ falhas não corrompem JSON nem barram os demais
def test_falha_de_um_coletor_nao_impede_atualizacao_dos_outros_e_json_continua_valido(docs_semeados, relogio):
    base = docs_semeados
    icti_antes = (base / "icti.json").read_bytes()
    doc = arm.carregar("ist", base)
    regs = [Registro(e["competencia"], e["indice"], e["var_mes"]) for e in doc["serie"]]
    novo = Registro("2026-10", round(regs[-1].indice * 1.004, 3), 0.40)
    cols = {"ipca": Fixo(erro=RuntimeError("falha inesperada")), "icti": Fixo(erro=ErroColeta("fonte fora do ar")),
            "ist": Fixo(ResultadoColeta(regs + [novo], {}, None, []))}
    res = executar(None, base=base, coletores=cols, relogio=relogio, eco=None, gatilho="agendado")
    por = {e["indice"]: e["resultado"] for e in res["entradas"]}
    assert por == {"ipca": "erro", "icti": "erro", "ist": "sucesso"}
    assert arm.carregar("ist", base)["ultima_competencia"] == "2026-10"       # o índice saudável foi atualizado
    assert (base / "icti.json").read_bytes() == icti_antes                    # o que falhou ficou intacto
    for nome in ("ipca", "icti", "ist", "status"):
        json.loads((base / f"{nome}.json").read_text(encoding="utf-8"))       # todos continuam JSON válido
    assert not list(base.glob("*.tmp")) and not (base / ".atualizacao.lock").exists()


def test_erro_durante_a_gravacao_nao_corrompe_o_arquivo(docs_semeados, monkeypatch):
    base = docs_semeados
    antes = (base / "ipca.json").read_bytes()

    def quebra(*a, **k):
        raise OSError("disco cheio")
    monkeypatch.setattr(arm.os, "replace", quebra)
    with pytest.raises(OSError):
        arm.escrever_json_atomico(base / "ipca.json", "{ truncado")
    assert (base / "ipca.json").read_bytes() == antes and not list(base.glob("ipca.json.*.tmp"))


def test_erro_inesperado_ao_gravar_vira_falha_do_indice_sem_afetar_os_demais(docs_semeados, relogio, monkeypatch):
    base = docs_semeados
    doc = arm.carregar("ist", base)
    regs = [Registro(e["competencia"], e["indice"], e["var_mes"]) for e in doc["serie"]]
    novo = Registro("2026-10", round(regs[-1].indice * 1.004, 3), 0.40)
    original = arm.salvar

    def salvar_com_defeito(codigo, d, b=None):
        if codigo == "ist":
            raise OSError("falha de gravação simulada")
        return original(codigo, d, b)
    monkeypatch.setattr(arm, "salvar", salvar_com_defeito)
    cols = legado(); cols["ist"] = Fixo(ResultadoColeta(regs + [novo], {}, None, []))
    res = executar(None, base=base, coletores=cols, relogio=relogio, eco=None, gatilho="agendado")
    por = {e["indice"]: e["resultado"] for e in res["entradas"]}
    assert por["ist"] == "erro" and por["ipca"] == "sucesso" and por["icti"] == "sucesso"
    assert arm.carregar("ist", base)["ultima_competencia"] == "2026-09"
