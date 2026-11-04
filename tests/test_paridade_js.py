import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js não instalado")
def test_calculo_js_identico_ao_python():
    r = subprocess.run(["node", str(RAIZ / "tests" / "paridade_calc.mjs")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 divergência(s)" in r.stdout
