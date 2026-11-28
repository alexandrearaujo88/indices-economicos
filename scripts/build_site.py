"""Monta site/ (interface + dados) para publicação no GitHub Pages ou qualquer hospedagem estática."""
import os
import shutil
from pathlib import Path

raiz = Path(__file__).resolve().parents[1]
site = Path(os.environ.get("SITE_DIR", raiz / "site"))            # saída (testes usam uma pasta temporária)
dados = Path(os.environ.get("INDICES_DATA_DIR", raiz / "data"))   # dados (testes usam uma cópia determinística)
if site.exists():
    shutil.rmtree(site)
shutil.copytree(raiz / "web", site)
shutil.copytree(dados, site / "data", ignore=shutil.ignore_patterns("*.jsonl", "*.lock", "*.tmp"))
(site / ".nojekyll").touch()
print(f"Site montado em {site} ({sum(1 for _ in site.rglob('*') if _.is_file())} arquivos)")
