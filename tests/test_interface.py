"""Interface no navegador (Chromium/Playwright) sobre o site montado."""
import functools
import http.server
import subprocess
import sys
import threading
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")
RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def url():
    subprocess.run([sys.executable, str(RAIZ / "scripts" / "build_site.py")], check=True, capture_output=True)
    h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(RAIZ / "site"))
    h.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


@pytest.fixture(scope="module")
def navegador():
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def pagina(navegador, url):
    ctx = navegador.new_context(locale="pt-BR", timezone_id="America/Maceio", viewport={"width": 1280, "height": 900})
    pg = ctx.new_page()
    pg.erros = []
    pg.on("console", lambda m: pg.erros.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: pg.erros.append(str(e)))
    pg.goto(url)
    pg.wait_for_selector(".ind")
    yield pg
    ctx.close()


def test_painel_mostra_tres_indices_sem_erros(pagina):
    assert pagina.locator(".ind").count() == 3
    txt = pagina.inner_text("main")
    assert "4,58%" in txt and "3,89%" in txt and "4,08%" in txt          # 12 meses publicados/validados
    assert "setembro de 2026" in txt and "agosto de 2026" in txt
    assert "Última coleta bem-sucedida" in txt
    assert pagina.erros == []


def test_calculadora_resultado_e_erros(pagina):
    pagina.goto(pagina.url.split("#")[0] + "#/calculadora")
    pagina.select_option("#c-ind", "ipca")
    pagina.fill("#c-ini", "012025"); pagina.fill("#c-fim", "122025"); pagina.fill("#c-val", "1.000,00")
    assert pagina.input_value("#c-ini") == "01/2025"                       # máscara
    pagina.click("button[type=submit]")
    r = pagina.inner_text("#c-res")
    assert "4,26%" in r and "R$ 1.042,64" in r.replace("\xa0", " ")
    pagina.fill("#c-ini", "12/2025"); pagina.fill("#c-fim", "01/2025"); pagina.click("button[type=submit]")
    assert "posterior" in pagina.inner_text("#c-res")                      # o original devolvia 0,00%
    pagina.select_option("#c-ind", "icti"); pagina.fill("#c-ini", "01/2026"); pagina.fill("#c-fim", "09/2026"); pagina.click("button[type=submit]")
    assert "ainda não foi divulgado" in pagina.inner_text("#c-res")
    pagina.fill("#c-ini", "13/2026"); pagina.click("button[type=submit]")
    assert "Data inválida" in pagina.inner_text("#c-res")
    assert pagina.erros == []


def test_calculadora_modo_data_base(pagina):
    pagina.goto(pagina.url.split("#")[0] + "#/calculadora")
    pagina.select_option("#c-ind", "ipca"); pagina.fill("#c-ini", "09/2025"); pagina.fill("#c-fim", "09/2026")
    pagina.check("input[value=data_base]"); pagina.click("button[type=submit]")
    assert "4,58%" in pagina.inner_text("#c-res")


def test_series_ordem_decrescente_e_formatacao(pagina):
    pagina.goto(pagina.url.split("#")[0] + "#/indices/ist")
    pagina.wait_for_selector("tbody tr")
    primeiras = pagina.locator("tbody tr").evaluate_all("rs => rs.slice(0,3).map(r => r.innerText)")
    assert primeiras[0].startswith("set/2026") and "362,701" in primeiras[0] and primeiras[1].startswith("ago/2026")
    assert "-0,37%" in primeiras[1].replace("−", "-")
    assert pagina.locator("tbody tr").count() == 249
    assert pagina.locator("svg.grafico path.linha").count() == 1
    pagina.select_option("#f-ano", "2014")
    assert pagina.locator("tbody tr").count() == 12 and "jun/2014" in pagina.inner_text("tbody")
    assert pagina.erros == []


def test_logs_consolidado_ordem_e_detalhes(pagina):
    pagina.goto(pagina.url.split("#")[0] + "#/logs")
    pagina.wait_for_selector("tbody tr")
    datas = pagina.locator("tbody tr td:first-child").all_inner_texts()
    assert len(datas) >= 3
    from datetime import datetime
    ts = [datetime.strptime(d.strip(), "%d/%m/%Y %H:%M") for d in datas]
    assert ts == sorted(ts, reverse=True)
    pagina.locator("button.tg").first.click()
    pagina.wait_for_selector("tr.det li")
    assert pagina.locator("tr.det li").count() >= 3
    pagina.click("[data-aba-log=icti]")
    pagina.wait_for_selector("[data-aba-log=icti][aria-pressed=true]")
    assert all(c.strip() == "ICTI" for c in pagina.locator("tbody tr td:nth-child(2)").all_inner_texts())
    assert pagina.erros == []


def test_mobile_sem_rolagem_horizontal(navegador, url):
    ctx = navegador.new_context(viewport={"width": 375, "height": 800}, device_scale_factor=2, locale="pt-BR")
    pg = ctx.new_page(); pg.goto(url); pg.wait_for_selector(".ind")
    for rota in ("#/painel", "#/calculadora", "#/indices/ipca", "#/logs", "#/sobre"):
        pg.goto(url + rota); pg.reload(); pg.wait_for_selector("main h2")
        assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), rota
    ctx.close()


def test_falha_de_dados_mostra_estado_de_erro(navegador, url):
    ctx = navegador.new_context(locale="pt-BR")
    pg = ctx.new_page()
    pg.route("**/data/*.json", lambda r: r.fulfill(status=500))
    pg.goto(url); pg.wait_for_selector("main .vazio")
    assert "Não foi possível carregar" in pg.inner_text("main")
    ctx.close()


def test_status_atrasado_e_exibido(navegador, url):
    import json, urllib.request
    st = json.load(urllib.request.urlopen(url + "data/status.json"))
    st["execucao"]["fim"] = "2026-10-01T09:00:00-03:00"
    ctx = navegador.new_context(locale="pt-BR")
    pg = ctx.new_page()
    pg.route("**/data/status.json", lambda r: r.fulfill(json=st))
    pg.goto(url); pg.wait_for_selector(".ind")
    assert "Atualização atrasada" in pg.inner_text("#estado-sistema")
    ctx.close()
