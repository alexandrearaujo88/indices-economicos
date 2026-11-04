"""O agendamento em nuvem deve disparar às 09h e 19h de America/Maceio."""
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from indices.config import FUSO, HORARIOS
from indices.orquestrador import _ler_status  # noqa: F401  (garante import do orquestrador)

RAIZ = Path(__file__).resolve().parents[1]
WF = (RAIZ / ".github" / "workflows" / "atualizar.yml").read_text(encoding="utf-8")


def horas_utc_dos_crons():
    crons = re.findall(r'-\s*cron:\s*"([^"]+)"', WF)
    out = {}
    for c in crons:
        minuto, horas, *resto = c.split()
        assert resto == ["*", "*", "*"], "agendamento deve ser diário"
        out[minuto] = [int(h) for h in horas.split(",")]
    return out


def test_cron_principal_equivale_a_09h_e_19h_em_maceio():
    principal = horas_utc_dos_crons()["0"]
    tz = ZoneInfo(FUSO)
    locais = []
    for h in principal:
        for mes in (1, 6, 12):                 # sem horário de verão: igual o ano todo
            dt = datetime(2026, mes, 15, h, 0, tzinfo=timezone.utc).astimezone(tz)
            locais.append(dt.strftime("%H:%M"))
    assert sorted(set(locais)) == sorted(HORARIOS) == ["09:00", "19:00"]


def test_reserva_so_roda_se_nao_houve_execucao_recente():
    assert "45 12,22" in WF and "--pular-se-recente 120" in WF


def test_ha_execucao_manual_e_trava_de_concorrencia():
    assert "workflow_dispatch" in WF and "cancel-in-progress: false" in WF and "group: atualizar-indices" in WF


def test_publicacao_roda_mesmo_se_um_indice_falhar_e_unico_segredo_e_opcional():
    assert "!cancelled()" in WF
    assert sorted(set(re.findall(r"secrets\.(\w+)", WF))) == ["HEALTHCHECK_URL"]       # opcional (sinal de vida externo)
    assert "if: ${{ env.HEALTHCHECK_URL != '' }}" in WF                              # sem o segredo, o passo é ignorado


def test_execucoes_automaticas_principal_e_reserva_evitam_coleta_duplicada():
    # tanto a agendada atrasada (depois da reserva) quanto a reserva (depois da principal) são ignoradas
    assert WF.count("--pular-se-recente 120") == 2
    assert "--gatilho agendado --pular-se-recente 120" in WF and "--gatilho reserva --pular-se-recente 120" in WF


def test_falha_ao_enviar_commit_de_dados_nao_passa_despercebida():
    assert 'enviado=1; break' in WF and '[ "$enviado" != "1" ]' in WF and "exit 1" in WF
    assert "git pull --rebase" in WF and 'git push origin "HEAD:${GITHUB_REF_NAME}"' in WF


def test_falha_de_coleta_deixa_o_fluxo_vermelho_e_o_monitoramento_roda_mesmo_assim():
    assert "Falhar o job se algum índice falhou" in WF and "steps.rodar.outputs.resultado != '0'" in WF
    mon = WF.split("  monitorar:")[1]
    assert "always()" in mon and "needs.atualizar.result != 'cancelled'" in mon       # roda mesmo com o job de coleta falho
    assert "python -m indices alertas execucao" in mon and "outputs.gravacao" in mon and "outputs.resultado" in mon
    assert "id: gravar" in WF and "gravacao: ${{ steps.gravar.outcome }}" in WF


def test_sem_actions_em_node20_e_runners_fixos():
    minimas = {"checkout": 7, "setup-python": 7, "setup-node": 7, "configure-pages": 6,
               "upload-pages-artifact": 5, "deploy-pages": 5}
    for arq in sorted((RAIZ / ".github" / "workflows").glob("*.yml")):
        txt = arq.read_text(encoding="utf-8")
        for nome, ver in re.findall(r"uses:\s*actions/([\w-]+)@v(\d+)", txt):
            assert int(ver) >= minimas[nome], f"{arq.name}: actions/{nome}@v{ver} abaixo da versão Node 24 ({minimas[nome]})"
        assert "ubuntu-latest" not in txt, f"{arq.name}: fixe a versão do runner"
        assert txt.count("upload-pages-artifact") == txt.count("include-hidden-files: true")


def test_permissoes_minimas():
    assert re.search(r"(?m)^permissions:\n  contents: read\n", WF)             # padrão do fluxo: somente leitura
    jobs = WF.split("\njobs:\n", 1)[1]
    por_job = {m.group(1): m.group(0) for m in re.finditer(r"(?ms)^  (\w[\w-]*):\n.*?(?=^  \w[\w-]*:\n|\Z)", jobs)}
    assert set(por_job) == {"atualizar", "publicar", "monitorar"}
    assert "contents: write" in por_job["atualizar"] and "contents: write" not in por_job["publicar"] + por_job["monitorar"]
    assert "issues: write" in por_job["monitorar"] and "issues: write" not in por_job["atualizar"] + por_job["publicar"]
    assert "pages: write" in por_job["publicar"] and "pages: write" not in por_job["atualizar"] + por_job["monitorar"]


def test_deploys_do_pages_sao_serializados_e_usam_a_ponta_da_branch():
    testes = (RAIZ / ".github" / "workflows" / "testes.yml").read_text(encoding="utf-8")
    assert "group: pages" in WF and "group: pages" in testes
    assert "ref: ${{ github.ref_name }}" in testes.split("publicar-interface:")[1]    # nunca o SHA antigo do push


def test_vigia_roda_depois_da_tolerancia_de_cada_horario_agendado():
    from indices import alertas as al
    vig = (RAIZ / ".github" / "workflows" / "vigiar.yml").read_text(encoding="utf-8")
    cron = re.search(r'cron:\s*"([^"]+)"', vig).group(1)
    minuto, horas, *_ = cron.split()
    execucoes = sorted((int(h) * 60 + int(minuto)) % 1440 for h in horas.split(","))
    esperadas = sorted((h * 60 + al.TOLERANCIA_MIN) % 1440 for h in al.SLOTS_UTC)
    assert execucoes == esperadas == [15, 14 * 60 + 15]
    assert al.COBERTURA_MIN == 120 and "--pular-se-recente 120" in WF           # mesma janela da reserva


def test_proxima_execucao_do_front_coincide_com_o_agendamento():
    import shutil, subprocess
    if shutil.which("node") is None:
        return
    js = ("import {proximaExecucao as p} from './web/js/format.js';"
          "console.log([p(new Date('2026-11-04T11:59:00Z')),p(new Date('2026-11-04T12:00:00Z')),p(new Date('2026-11-04T22:00:00Z'))].join(' '))")
    raiz = Path(__file__).resolve().parents[1]
    out = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, cwd=raiz).stdout.split()
    assert out == ["2026-11-04T12:00:00.000Z", "2026-11-04T22:00:00.000Z", "2026-11-05T12:00:00.000Z"]
