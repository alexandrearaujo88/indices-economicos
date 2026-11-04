"""Cliente HTTP com timeout, tentativas controladas e registro de cada etapa."""
from __future__ import annotations

import time
from typing import Callable

import requests

from .config import USER_AGENT
from .modelos import ErroColeta

TIMEOUT = (10, 60)          # (conexão, leitura) em segundos
TENTATIVAS = 3
ESPERAS = (2.0, 6.0)        # espera entre tentativas (backoff simples)
STATUS_TRANSITORIOS = {408, 425, 429, 500, 502, 503, 504}
TAMANHO_MAX = 50 * 1024 * 1024


def nova_sessao() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json,application/octet-stream;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9",
    })
    return s


def obter(sessao: requests.Session, url: str, *, log: Callable[..., None] | None = None,
          tentativas: int = TENTATIVAS, timeout=TIMEOUT, esperas=ESPERAS,
          permitir_tls_inseguro: bool = False, dormir: Callable[[float], None] = time.sleep,
          params: dict | None = None) -> requests.Response:
    """GET com tentativas. Lança ErroColeta se esgotar as tentativas.

    - 4xx (exceto 408/425/429) não é repetido: indica URL/estrutura errada.
    - Falha de certificado só é contornada se `permitir_tls_inseguro` (e fica registrada
      como aviso); nunca silenciosamente.
    """
    log = log or (lambda *a, **k: None)
    verify = True
    ultimo = "falha desconhecida"
    for n in range(1, tentativas + 1):
        try:
            r = sessao.get(url, timeout=timeout, verify=verify, params=params, stream=False)
        except requests.exceptions.SSLError as e:
            ultimo = f"erro de certificado TLS ({type(e).__name__})"
            if permitir_tls_inseguro and verify:
                verify = False
                log("Certificado TLS da fonte não pôde ser verificado; repetindo sem verificação "
                    "(fonte pública, dados validados após o download)", nivel="aviso", tentativa=n)
                continue
            log(f"Tentativa {n}/{tentativas}: {ultimo}", nivel="aviso")
        except requests.exceptions.Timeout:
            ultimo = f"tempo esgotado (limite {timeout[0]}s conexão / {timeout[1]}s leitura)"
            log(f"Tentativa {n}/{tentativas}: {ultimo}", nivel="aviso")
        except requests.exceptions.RequestException as e:
            ultimo = f"falha de rede ({type(e).__name__})"
            log(f"Tentativa {n}/{tentativas}: {ultimo}", nivel="aviso")
        else:
            if r.status_code == 200:
                if len(r.content) > TAMANHO_MAX:
                    raise ErroColeta("Resposta maior que o limite de segurança (50 MB)")
                if not verify:
                    log("Download concluído SEM verificação de certificado TLS", nivel="aviso")
                return r
            ultimo = f"HTTP {r.status_code}"
            if r.status_code not in STATUS_TRANSITORIOS:
                raise ErroColeta(f"{ultimo} ao acessar {_curto(url)}")
            log(f"Tentativa {n}/{tentativas}: {ultimo}", nivel="aviso")
        if n < tentativas:
            dormir(esperas[min(n - 1, len(esperas) - 1)])
    raise ErroColeta(f"Falha após {tentativas} tentativas em {_curto(url)}: {ultimo}")


def _curto(url: str) -> str:
    return url.split("?")[0]
