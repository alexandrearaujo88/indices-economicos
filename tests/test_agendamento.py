"""O agendamento em nuvem deve disparar às 09h e 19h de America/Maceio."""
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from indices.config import FUSO, HORARIOS
from indices.orquestrador import _ler_status  # noqa: F401  (garante import do orquestrador)

WF = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "atualizar.yml").read_text(encoding="utf-8")


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


def test_publicacao_roda_mesmo_se_um_indice_falhar_e_nao_usa_segredos():
    assert "!cancelled()" in WF and "secrets." not in WF


def test_proxima_execucao_do_front_coincide_com_o_agendamento():
    import shutil, subprocess
    if shutil.which("node") is None:
        return
    js = ("import {proximaExecucao as p} from './web/js/format.js';"
          "console.log([p(new Date('2026-11-04T11:59:00Z')),p(new Date('2026-11-04T12:00:00Z')),p(new Date('2026-11-04T22:00:00Z'))].join(' '))")
    raiz = Path(__file__).resolve().parents[1]
    out = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, cwd=raiz).stdout.split()
    assert out == ["2026-11-04T12:00:00.000Z", "2026-11-04T22:00:00.000Z", "2026-11-05T12:00:00.000Z"]
