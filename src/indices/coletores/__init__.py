from .base import Coletor
from .icti import ColetorICTI
from .ipca import ColetorIPCA
from .ist import ColetorIST

COLETORES = {"ipca": ColetorIPCA, "icti": ColetorICTI, "ist": ColetorIST}

__all__ = ["Coletor", "COLETORES", "ColetorIPCA", "ColetorICTI", "ColetorIST"]
