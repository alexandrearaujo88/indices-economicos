"""Alertas por issue: o que dispara, o que não dispara e a ausência de notificações repetidas."""
import json
from datetime import datetime, timedelta, timezone

from indices import alertas as al

CTX = al.Contexto(repo="dono/indices-economicos", dono="dono", run_id="123")
T0 = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)          # início do run
ISO = lambda dt: dt.astimezone(timezone(timedelta(hours=-3))).isoformat()


def status(resultados: dict, quando=T0 + timedelta(minutes=2), fim=None):
    return {"execucao": {"fim": ISO(fim or quando)},
            "indices": {c: {"resultado": r, "ultima_tentativa": ISO(quando), "mensagem": f"Falha na coleta: {c} fora do ar" if r == "erro" else "ok",
                            "ultima_coleta_ok": ISO(T0 - timedelta(hours=12))}
                        for c, r in resultados.items()}}


class GhFalso:
    """Simula a CLI `gh` mantendo as issues em memória."""
    def __init__(self, abertas=None):
        self.issues = {n: {"number": n, "body": b, "aberta": True} for n, b in (abertas or {}).items()}
        self.chamadas = []
        self.prox = 100

    def __call__(self, args):
        self.chamadas.append(args)
        if args[:2] == ["issue", "list"]:
            return json.dumps([{"number": i["number"], "body": i["body"]} for i in self.issues.values() if i["aberta"]])
        if args[:2] == ["issue", "create"]:
            self.prox += 1
            self.issues[self.prox] = {"number": self.prox, "body": args[args.index("--body") + 1], "aberta": True}
        elif args[:2] == ["issue", "edit"]:
            self.issues[int(args[2])]["body"] = args[args.index("--body") + 1]
        elif args[:2] == ["issue", "close"]:
            self.issues[int(args[2])]["aberta"] = False
        return ""

    def acoes(self, tipo):
        return [c for c in self.chamadas if c[:2] == ["issue", tipo]]


def avaliar(st, atualizar="success", publicar="success", **kw):
    return al.avaliar_execucao(st, resultado_atualizar=atualizar, resultado_publicar=publicar, inicio_run=T0, ctx=CTX, **kw)


def test_falha_de_um_coletor_gera_alerta_com_indice_horario_motivo_e_links():
    ativos, avaliadas = avaliar(status({"ipca": "erro", "icti": "sucesso", "ist": "sucesso"}))
    assert set(ativos) == {"coletor-ipca"} and avaliadas >= {"coletor-ipca", "coletor-icti", "coletor-ist", "workflow"}
    inc = ativos["coletor-ipca"]
    assert "IPCA" in inc.titulo and "@dono" in inc.corpo
    assert "05/11/2026 09:02 (Maceió)" in inc.corpo                      # horário local (UTC−3)
    assert "ipca fora do ar" in inc.corpo                                 # motivo
    assert "https://github.com/dono/indices-economicos/actions/runs/123" in inc.corpo
    assert "https://dono.github.io/indices-economicos/#/logs/ipca" in inc.corpo


def test_aviso_e_ausencia_de_novidades_nao_geram_alerta():
    ativos, _ = avaliar(status({"ipca": "aviso", "icti": "sucesso", "ist": "sucesso"}))
    assert ativos == {}


def test_indice_nao_executado_nesta_rodada_nao_e_avaliado():
    antigo = status({"ipca": "erro", "icti": "sucesso", "ist": "sucesso"}, quando=T0 - timedelta(days=1))
    ativos, avaliadas = avaliar(antigo)
    assert ativos == {} and not any(k.startswith("coletor-") for k in avaliadas)   # execução ignorada não "cura" alertas


def test_falha_estrutural_do_fluxo_ou_da_publicacao_gera_alerta_de_workflow():
    st = status({"ipca": "sucesso", "icti": "sucesso", "ist": "sucesso"})
    a, _ = avaliar(st, atualizar="failure")
    p, _ = avaliar(st, publicar="failure")
    assert "workflow" in a and "workflow" in p and "publicação" in p["workflow"].corpo
    c, av = avaliar(st, atualizar="cancelled", publicar="skipped")
    assert c == {} and "workflow" not in av                                 # cancelamento não é erro


def test_simulacao_nao_fecha_alerta_de_workflow():
    _, av = avaliar(status({"ipca": "sucesso", "icti": "sucesso", "ist": "sucesso"}), publicar="skipped")
    assert "workflow" not in av


def test_ciclo_sem_notificacoes_repetidas():
    gh = GhFalso()
    ativos, av = avaliar(status({"ipca": "erro", "icti": "sucesso", "ist": "sucesso"}))
    assert al.sincronizar(ativos, av, gh) == [("abrir", "coletor-ipca")]
    assert len(gh.acoes("create")) == 1
    # mesma falha na rodada seguinte (outro horário/ link): só edita a issue existente, sem criar nem comentar
    global T0
    ant = T0
    try:
        T0 = ant + timedelta(hours=10)
        ativos2, av2 = al.avaliar_execucao(status({"ipca": "erro", "icti": "sucesso", "ist": "sucesso"}, quando=T0 + timedelta(minutes=1)),
                                           resultado_atualizar="success", resultado_publicar="success", inicio_run=T0, ctx=CTX)
    finally:
        T0 = ant
    assert al.sincronizar(ativos2, av2, gh) == [("atualizar", "coletor-ipca")]
    assert len(gh.acoes("create")) == 1 and not any("--comment" in c for c in gh.chamadas if c[:2] == ["issue", "edit"])
    # repetição idêntica: nenhuma chamada de escrita
    n = len(gh.chamadas)
    assert al.sincronizar(ativos2, av2, gh) == []
    assert all(c[:2] in (["issue", "list"], ["label", "create"]) for c in gh.chamadas[n:])
    # recuperação: fecha a issue com comentário
    ok, av3 = avaliar(status({"ipca": "sucesso", "icti": "sucesso", "ist": "sucesso"}))
    assert al.sincronizar(ok, av3, gh) == [("fechar", "coletor-ipca")]
    assert not any(i["aberta"] for i in gh.issues.values())
    assert al.sincronizar(ok, av3, gh) == []                                # nada mais a fechar


def test_alertas_de_indices_diferentes_sao_independentes():
    gh = GhFalso()
    ativos, av = avaliar(status({"ipca": "erro", "icti": "erro", "ist": "sucesso"}))
    assert sorted(al.sincronizar(ativos, av, gh)) == [("abrir", "coletor-icti"), ("abrir", "coletor-ipca")]


def test_issue_duplicada_por_corrida_e_encerrada():
    gh = GhFalso()
    ativos, av = avaliar(status({"ipca": "erro", "icti": "sucesso", "ist": "sucesso"}))
    corpo = al._corpo_final(ativos["coletor-ipca"])
    gh = GhFalso({5: corpo, 9: corpo})
    acoes = al.sincronizar(ativos, av, gh)
    assert acoes == [("fechar", "coletor-ipca")] and gh.issues[5]["aberta"] and not gh.issues[9]["aberta"]


def test_simular_nao_escreve():
    gh = GhFalso()
    ativos, av = avaliar(status({"ipca": "erro", "icti": "sucesso", "ist": "sucesso"}))
    assert al.sincronizar(ativos, av, gh, simular=True) == [("abrir", "coletor-ipca")]
    assert gh.acoes("create") == []


# ------------------------------------------------------------------ vigia de execuções agendadas
def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_ultimo_slot():
    assert al.ultimo_slot(utc(2026, 11, 5, 14, 15)) == utc(2026, 11, 5, 12)
    assert al.ultimo_slot(utc(2026, 11, 5, 23, 0)) == utc(2026, 11, 5, 22)
    assert al.ultimo_slot(utc(2026, 11, 6, 0, 15)) == utc(2026, 11, 5, 22)
    assert al.ultimo_slot(utc(2026, 11, 5, 3, 0)) == utc(2026, 11, 4, 22)


def test_vigia_nao_julga_antes_da_tolerancia():
    st = {"execucao": {"fim": "2026-11-04T19:01:00-03:00"}}
    assert al.avaliar_atraso(st, utc(2026, 11, 5, 13, 0), CTX) == ({}, set())     # só 60 min após o horário das 12h UTC: cedo para julgar


def test_vigia_detecta_execucao_ausente():
    st = {"execucao": {"fim": "2026-11-04T19:01:00-03:00"}}                       # 22:01 UTC de ontem
    ativos, av = al.avaliar_atraso(st, utc(2026, 11, 5, 14, 15), CTX)
    assert av == {"atraso"} and "atraso" in ativos
    corpo = ativos["atraso"].corpo
    assert "05/11/2026 09:00 (Maceió)" in corpo and "04/11/2026 19:01 (Maceió)" in corpo and "@dono" in corpo


def test_vigia_aceita_execucao_principal_reserva_e_atrasada():
    for fim in ("2026-11-05T09:01:00-03:00", "2026-11-05T09:47:00-03:00", "2026-11-05T11:10:00-03:00"):
        ativos, av = al.avaliar_atraso({"execucao": {"fim": fim}}, utc(2026, 11, 5, 14, 15), CTX)
        assert ativos == {} and av == {"atraso"}, fim


def test_vigia_considera_execucao_recente_que_fez_a_agendada_ser_ignorada():
    # manual às 10h40 local (13h40 UTC)... mas a janela de cobertura é de 120 min antes do horário
    ativos, _ = al.avaliar_atraso({"execucao": {"fim": "2026-11-05T07:30:00-03:00"}}, utc(2026, 11, 5, 14, 15), CTX)
    assert ativos == {}                                                           # 10:30 UTC >= 12:00 − 120 min


def test_vigia_sem_status_alerta_e_status_ilegivel_tambem():
    assert "atraso" in al.avaliar_atraso(None, utc(2026, 11, 5, 14, 15), CTX)[0]
    assert "atraso" in al.avaliar_atraso({"execucao": {"fim": "lixo"}}, utc(2026, 11, 5, 14, 15), CTX)[0]


def test_vigia_fecha_alerta_quando_a_execucao_ocorre():
    gh = GhFalso()
    ativos, av = al.avaliar_atraso(None, utc(2026, 11, 5, 14, 15), CTX)
    assert al.sincronizar(ativos, av, gh) == [("abrir", "atraso")]
    ativos2, av2 = al.avaliar_atraso({"execucao": {"fim": "2026-11-05T19:05:00-03:00"}}, utc(2026, 11, 6, 0, 15), CTX)
    assert ativos2 == {} and al.sincronizar(ativos2, av2, gh) == [("fechar", "atraso")]


def test_teste_do_canal_abre_e_encerra_sem_tocar_nos_alertas_reais():
    gh = GhFalso()
    ativos, av = al.avaliar_teste(True, CTX)
    assert al.sincronizar(ativos, av, gh) == [("abrir", "teste")] and "@dono" in ativos["teste"].corpo
    ativos, av = al.avaliar_teste(False, CTX)
    assert al.sincronizar(ativos, av, gh) == [("fechar", "teste")]


def test_fluxo_vermelho_por_falha_de_coletor_gera_so_o_alerta_do_coletor():
    st = status({"ipca": "erro", "icti": "sucesso", "ist": "sucesso"})
    ativos, av = avaliar(st, atualizar="failure", coleta="1", gravacao="success")
    assert set(ativos) == {"coletor-ipca"} and "workflow" in av        # nenhum alerta duplicado de "workflow"


def test_fluxo_vermelho_por_outro_motivo_gera_alerta_de_workflow_alem_do_coletor():
    st = status({"ipca": "erro", "icti": "sucesso", "ist": "sucesso"})
    ativos, _ = avaliar(st, atualizar="failure", coleta="1", gravacao="failure")      # falha ao gravar no repositório
    assert set(ativos) == {"coletor-ipca", "workflow"}
    ativos, _ = avaliar(st, atualizar="failure", coleta="", gravacao="")             # falhou antes de coletar (ex.: pip)
    assert "workflow" in ativos


def test_fluxo_so_com_falha_de_coletor_fecha_alerta_de_workflow_antigo():
    gh = GhFalso()
    st_ok = status({"ipca": "sucesso", "icti": "sucesso", "ist": "sucesso"})
    ativos, av = avaliar(st_ok, atualizar="failure")                                  # erro estrutural
    assert al.sincronizar(ativos, av, gh) == [("abrir", "workflow")]
    st = status({"ipca": "erro", "icti": "sucesso", "ist": "sucesso"})
    ativos, av = avaliar(st, atualizar="failure", coleta="1", gravacao="success")
    assert sorted(al.sincronizar(ativos, av, gh)) == [("abrir", "coletor-ipca"), ("fechar", "workflow")]
