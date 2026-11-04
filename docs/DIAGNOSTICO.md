# Diagnóstico do projeto original (Apps Script + Python/Windows)

## Como funcionava
1. **Windows (PC do usuário)**: tarefa agendada (`Executar main_indices_upload.py.xml`, 10h e 22h no relógio do PC; os logs mostram 09h e 21h em Maceió) chamava `main_indices_upload.py`, que rodava seis scripts em sequência (`ipca.py`, `trata_ipca.py`, `icti.py`, `trata_icti.py`, `ist.py`, `trata_ist.py`) e copiava a pasta `indices/` para o Google Drive (`X:\Meu Drive\...`). O `.bat` só chamava o mesmo script.
2. **Coleta**: IPCA = ZIP/XLS do FTP do IBGE; ICTI = varredura do blog Carta de Conjuntura (Ipea) e download do XLSX; IST = tabelas HTML da página da Anatel.
3. **Tratamento**: cada índice virava `*.xlsx` (Ano, Mês, Índice, Var. mês, Var. 12 meses).
4. **Google**: `Código.gs` convertia os XLSX em Google Sheets (só se a data do arquivo mudasse), copiava para planilhas “_fixa” e para a planilha “Testador”; `index.html` mostrava planilhas publicadas em `<iframe>` e calculava acumulados via `google.script.run`.

## Problemas encontrados
| # | Problema | Efeito | Situação na nova versão |
|---|---|---|---|
| 1 | Dependência total de um PC Windows ligado (`StartWhenAvailable=false`) | Sem PC ligado, nada atualiza | GitHub Actions (nuvem) |
| 2 | **`trata_ist.py` descartava jun/2014 e mai/2017** (a fonte traz “jun/14” minúsculo e “Maio/17” por extenso; o mapa de meses não reconhecia) | 2 meses faltando; **23 valores de “Var. em 12 meses” errados (até 0,91 p.p.)**; acumulados que cruzam esses meses omitiam a variação | Parser normaliza; série completa (249 meses); teste dedicado |
| 3 | `calcularIndiceAcumuladoGS` compunha variações mensais **arredondadas a 2 casas** | Erro de arredondamento acumulado (ex.: ICTI 2013–2026: 109,3007% contra 109,3018% pelo índice) | Razão entre números-índice |
| 4 | Período invertido (inicial > final) devolvia “0,00%” sem aviso | Resultado enganoso | Erro explícito |
| 5 | Sem validação de mês (aceitava “13/2024”, “1/2023”) e `calcularIndice` (função antiga) lia o **dia como mês** | Resultados errados/“Período não disponível” | Validação MM/AAAA; função antiga removida |
| 6 | Valor monetário corrigido por IPCA a partir de períodos pré-Real (antes de jul/1994) | Resultado sem sentido (moeda diferente) | Percentual permitido, valor bloqueado com explicação |
| 7 | `icti.py` escolhia o 1º post cujo título tivesse QUALQUER palavra como “variação”/“percentual” + um mês; `requests.get` sem timeout (os logs mostram timeouts e travamentos); pegava o 1º `.xlsx` da página | Podia baixar post/arquivo errado; execução podia travar | Título estrito do ICTI; timeouts e 3 tentativas; escolha do link por nome |
| 8 | `ist.py` com `verify=False` incondicional | Sem verificação de certificado | Verificação ligada; contorno só em falha de certificado, com aviso no log |
| 9 | `main_indices.py` parava tudo se um `trata_*` falhasse; coletores engoliam erros e o tratamento seguia com arquivo velho | Falha de um índice impedia os outros / dado velho sem aviso | Execução isolada por índice, resultado explícito |
| 10 | Conversões desnecessárias (.xls→.xlsx→Google Sheets→“fixa”→“Testador”), pasta `lado-do-google` duplicada, `icti_bruto (1).xlsx` esquecido | Complexidade e fragilidade | JSON único por índice; sem intermediários |
| 11 | Variação em 12 meses calculada por `shift(12)` posicional | Quebra silenciosamente se faltar qualquer mês (como no item 2) | Cálculo por competência (mês − 12) |
| 12 | “Var. no mês” do IST gravada como texto `"- 0,22"` (jul/2023, espaço após o sinal) | Vira texto; `parseFloat` daria NaN | Conversor numérico robusto |
| 13 | IPCA: 12 valores de 12 meses de 1994 existem na fonte e eram descartados | Perda de informação publicada | Preservados |
| 14 | Dados históricos sem registro de revisões; o Ipea já revisou abr/2025 (índice 200,2862 → 200,2973) e a Anatel já corrigiu fev/2016 | Revisões sobrescritas sem rastro | Revisões detectadas e guardadas por competência |
| 15 | IDs de planilhas, pasta e URLs publicadas no código | Exposição desnecessária | Nada disso existe mais |

## Funcionalidades preservadas
Três índices; tabelas Ano/Mês/Índice/Var. mês/Var. 12 meses (mais novo primeiro); seleção de índice; cálculo de acumulado com período (MM/AAAA) e valor corrigido opcional; logs de coleta com data, arquivo e URL; duas execuções diárias.

## Observação sobre horários
Pedido: 09h e 19h (Maceió). O original rodava às 09h e 21h (logs) / 10h e 22h (XML). Foi implementado o pedido: 09h e 19h.
