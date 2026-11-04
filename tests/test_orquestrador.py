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
