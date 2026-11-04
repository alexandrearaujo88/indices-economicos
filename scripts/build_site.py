"""Monta site/ (interface + dados) para publicação no GitHub Pages ou qualquer hospedagem estática."""
import shutil
from pathlib import Path

raiz = Path(__file__).resolve().parents[1]
site = raiz / "site"
if site.exists():
    shutil.rmtree(site)
shutil.copytree(raiz / "web", site)
shutil.copytree(raiz / "data", site / "data", ignore=shutil.ignore_patterns("*.jsonl", "*.lock", "*.tmp"))
(site / ".nojekyll").touch()
print(f"Site montado em {site} ({sum(1 for _ in site.rglob('*') if _.is_file())} arquivos)")
