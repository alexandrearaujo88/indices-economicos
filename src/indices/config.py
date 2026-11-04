"""Configuração central: fuso, agendamento e cadastro dos três índices."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

FUSO = "America/Maceio"          # UTC−03:00, sem horário de verão desde 2019
TZ = ZoneInfo(FUSO)
HORARIOS = ("09:00", "19:00")    # horários oficiais de atualização (fuso acima)
SCHEMA_VERSION = 1
USER_AGENT = ("Mozilla/5.0 (compatible; IndicesDAGGTIC/1.0; coleta automatizada de "
              "índices econômicos públicos)")

RAIZ = Path(__file__).resolve().parents[2]


def dir_dados() -> Path:
    """Diretório dos JSON e logs (sobrescrevível por INDICES_DATA_DIR, usado nos testes)."""
    return Path(os.environ.get("INDICES_DATA_DIR", RAIZ / "data"))


@dataclass(frozen=True)
class IndiceCfg:
    codigo: str                       # chave de arquivo: ipca, ist, icti
    sigla: str
    nome: str
    orgao: str
    natureza: str
    base: str                         # texto da base do número-índice
    base_anterior: dict | None        # âncora do mês anterior ao 1º da série
    casas_indice: int                 # casas decimais para exibição
    inicio_serie: str | None          # AAAA-MM (corte do histórico)
    min_registros: int                # piso para aceitar uma coleta
    limite_var_mes: float             # |variação mensal| máxima plausível (%)
    fonte_nome: str
    fonte_url: str                    # página oficial de referência
    metodo_coleta: str
    metodologia_url: str
    metodologia_acumulado: str
    observacoes: tuple[str, ...] = field(default_factory=tuple)
    periodicidade: str = "mensal"
    tls_inseguro_permitido: bool = False
    atraso_alerta_dias: int = 75      # sem competência nova há mais que isso => aviso na UI

    def meta_json(self) -> dict:
        return {
            "codigo": self.codigo, "sigla": self.sigla, "nome": self.nome, "orgao": self.orgao,
            "natureza": self.natureza, "periodicidade": self.periodicidade,
            "base": self.base, "base_anterior": self.base_anterior,
            "casas_indice": self.casas_indice,
        }


INDICES: dict[str, IndiceCfg] = {
    "ipca": IndiceCfg(
        codigo="ipca", sigla="IPCA",
        nome="Índice Nacional de Preços ao Consumidor Amplo", orgao="IBGE",
        natureza="Índice de preços ao consumidor (inflação oficial). Número-índice encadeado "
                 "mensalmente; cada valor representa o nível de preços da competência.",
        base="dez/1993 = 100", base_anterior={"competencia": "1993-12", "indice": 100.0},
        casas_indice=2, inicio_serie="1994-01", min_registros=300, limite_var_mes=60.0,
        fonte_nome="IBGE — SIDRA, tabela 1737 (IPCA, série histórica)",
        fonte_url="https://www.ibge.gov.br/estatisticas/economicas/precos-e-custos/9256-indice-nacional-de-precos-ao-consumidor-amplo.html",
        metodo_coleta="Planilha da série histórica no FTP do IBGE (ZIP/XLS) como fonte primária; "
                      "API SIDRA (JSON) como contingência.",
        metodologia_url="https://www.ibge.gov.br/estatisticas/economicas/precos-e-custos/9256-indice-nacional-de-precos-ao-consumidor-amplo.html?=&t=o-que-e",
        metodologia_acumulado="Acumulado = razão entre números-índice (Índice do mês final ÷ Índice do mês "
                              "anterior ao inicial − 1), sem composição de variações arredondadas.",
        observacoes=(
            "A série começa em jan/1994 (transição para o Plano Real). Antes de jul/1994 os valores "
            "monetários estavam em outra moeda (cruzeiro real/URV); por isso o cálculo de valor corrigido "
            "é bloqueado para períodos que começam antes de jul/1994.",
        ),
    ),
    "icti": IndiceCfg(
        codigo="icti", sigla="ICTI",
        nome="Índice de Custo da Tecnologia da Informação", orgao="Ipea (DIMAC)",
        natureza="Índice de custos (não é índice de preços ao consumidor): mede a evolução dos custos "
                 "efetivos de TI, com 8 classes de despesa e pesos atualizados a cada janeiro. "
                 "Número-índice mensal.",
        base="dez/2012 = 100", base_anterior={"competencia": "2012-12", "indice": 100.0},
        casas_indice=2, inicio_serie="2013-01", min_registros=100, limite_var_mes=10.0,
        fonte_nome="Ipea — Carta de Conjuntura (série completa do ICTI)",
        fonte_url="https://www.ipea.gov.br/cartadeconjuntura/",
        metodo_coleta="Localização da publicação mais recente no blog Carta de Conjuntura e download "
                      "direto da planilha XLSX da série completa (leitura em memória, sem conversões).",
        metodologia_url="https://repositorio.ipea.gov.br/bitstream/11058/8320/4/cc_39_nt_indicador_ICTI_nota_metodologica.pdf",
        metodologia_acumulado="Acumulado = razão entre números-índice (Índice do mês final ÷ Índice do mês "
                              "anterior ao inicial − 1), com os valores completos publicados pelo Ipea.",
        observacoes=(
            "Publicado com defasagem de cerca de 40 a 50 dias; a última competência costuma ser "
            "anterior à do IPCA e do IST.",
            "O Ipea republica a série completa a cada divulgação e já revisou valores passados; "
            "revisões são preservadas no histórico de cada competência.",
        ),
    ),
    "ist": IndiceCfg(
        codigo="ist", sigla="IST",
        nome="Índice Setorial de Telecomunicações", orgao="Anatel",
        natureza="Índice setorial de variação de custos das telecomunicações, divulgado pela Anatel e "
                 "usado em reajustes de contratos e tarifas do setor. Número-índice mensal.",
        base="não informada pela Anatel", base_anterior=None,
        casas_indice=3, inicio_serie="2006-01", min_registros=200, limite_var_mes=10.0,
        fonte_nome="Anatel — Valores do IST",
        fonte_url="https://www.gov.br/anatel/pt-br/regulado/competicao/tarifas-e-precos/valores-do-ist",
        metodo_coleta="Leitura das tabelas HTML da página oficial da Anatel (não há API nem arquivo "
                      "estruturado publicado).",
        metodologia_url="https://www.gov.br/anatel/pt-br/regulado/competicao/tarifas-e-precos/valores-do-ist",
        metodologia_acumulado="Acumulado = razão entre números-índice (Índice do mês final ÷ Índice do mês "
                              "anterior ao inicial − 1). Para jan/2006 (primeira competência) o índice "
                              "anterior é reconstruído a partir da variação mensal publicada.",
        observacoes=(
            "A Anatel já corrigiu valores publicados (ex.: fev/2016); correções são detectadas e "
            "registradas como revisões.",
            "A página da Anatel não publica a variação em 12 meses; ela é calculada a partir dos "
            "números-índice.",
        ),
        tls_inseguro_permitido=True,
    ),
}

ORDEM_EXECUCAO = ("ipca", "icti", "ist")
