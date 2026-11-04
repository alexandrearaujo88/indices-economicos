"""Contrato dos coletores. Cada índice implementa a sua classe de forma independente."""
from __future__ import annotations

from typing import Callable

import requests

from ..config import IndiceCfg
from ..http import nova_sessao
from ..modelos import ResultadoColeta

Logger = Callable[..., None]


class Coletor:
    cfg: IndiceCfg

    def __init__(self, cfg: IndiceCfg, sessao: requests.Session | None = None,
                 dormir: Callable[[float], None] | None = None):
        self.cfg = cfg
        self.sessao = sessao or nova_sessao()
        self.dormir = dormir
        self._kw = {"dormir": dormir} if dormir else {}

    def coletar(self, log: Logger) -> ResultadoColeta:  # pragma: no cover - interface
        raise NotImplementedError
