import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js não instalado")
def test_calculo_js_identico_ao_python(tmp_path):
    # mesmos dados determinísticos usados para gerar os vetores (não os dados reais que o robô grava em data/)
    import os
    from conftest import FIX
    from indices.migracao import coletores_legado
    from indices.orquestrador import executar
    dados = tmp_path / "data"
    dados.mkdir()
    executar(None, base=dados, coletores=coletores_legado(FIX), eco=None)
    r = subprocess.run(["node", str(RAIZ / "tests" / "paridade_calc.mjs")], capture_output=True, text=True,
                       env={**os.environ, "INDICES_DATA_DIR": str(dados)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 divergência(s)" in r.stdout
