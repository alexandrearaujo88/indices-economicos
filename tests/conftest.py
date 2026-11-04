from __future__ import annotations

import io
import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
import requests

from indices.config import TZ

FIX = Path(__file__).parent / "fixtures"
RAIZ = Path(__file__).resolve().parents[1]


class FakeResp:
    def __init__(self, conteudo=b"", status=200, json_obj=None):
        self.status_code = status
        self.content = conteudo if isinstance(conteudo, bytes) else conteudo.encode("utf-8")
        self._json = json_obj

    @property
    def text(self):
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        if self._json is not None:
            return self._json
        return json.loads(self.text)


class FakeSession:
    """Substitui requests.Session: respostas programadas por prefixo de URL, consumidas em ordem."""

    def __init__(self, rotas: dict):
        self.rotas = {k: (list(v) if isinstance(v, list) else [v]) for k, v in rotas.items()}
        self.chamadas: list[dict] = []

    def get(self, url, timeout=None, verify=True, params=None, stream=False):
        self.chamadas.append({"url": url, "verify": verify})
        for prefixo in sorted(self.rotas, key=len, reverse=True):
            if url.startswith(prefixo):
                fila = self.rotas[prefixo]
                item = fila.pop(0) if len(fila) > 1 else fila[0]
                if isinstance(item, Exception):
                    raise item
                return item
        raise AssertionError(f"URL sem rota no teste: {url}")

    def n(self, prefixo: str) -> int:
        return sum(1 for c in self.chamadas if c["url"].startswith(prefixo))


@pytest.fixture
def dormir():
    esperas = []
    return esperas.append


@pytest.fixture
def base(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def relogio():
    """Relógio determinístico que avança 1 s a cada leitura (fuso America/Maceio)."""
    estado = {"t": datetime(2026, 11, 4, 9, 0, 0, tzinfo=TZ)}

    def agora():
        from datetime import timedelta
        estado["t"] = estado["t"] + timedelta(seconds=1)
        return estado["t"]
    agora.estado = estado
    return agora


def ler_xlsx_bytes(nome: str) -> bytes:
    return (FIX / nome).read_bytes()


def html_ist(linhas: list[tuple[str, str, str]], classe="slate-table-block", cabecalho=("Referência", "Variação", "IST")) -> str:
    trs = "".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td></tr>" for a, b, c in linhas)
    return (f"<html><head><title>Valores do IST</title></head><body><table class='{classe}'>"
            f"<tr><th>{cabecalho[0]}</th><th>{cabecalho[1]}</th><th>{cabecalho[2]}</th></tr>{trs}</table></body></html>")


def zip_com(nome: str, conteudo: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(nome, conteudo)
    return buf.getvalue()


@pytest.fixture
def docs_semeados(base):
    """Semeia data/ com os arquivos originais (usado por vários testes)."""
    from indices.migracao import coletores_legado
    from indices.orquestrador import executar
    executar(None, base=base, coletores=coletores_legado(FIX), eco=None)
    return base
