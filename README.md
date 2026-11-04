# Índices DAGGTIC — IPCA, ICTI e IST

Coleta automatizada (Python), armazenamento em JSON e interface web estática. Roda 100% na nuvem
(GitHub Actions + GitHub Pages); o computador do usuário pode ficar desligado indefinidamente.

```
GitHub Actions (09h e 19h Maceió)          Repositório (persistência)            GitHub Pages
  python -m indices atualizar   ──grava──►  data/ipca.json, icti.json, ist.json  ──►  site estático
  3 coletores independentes                 data/status.json                          (tabelas, gráfico,
  validação antes de gravar                 data/logs/<índice>/AAAA.jsonl              calculadora, logs)
                                            data/logs/consolidado.json
```

## Por que NÃO há backend permanente (Alternativa A escolhida)
| Requisito | Alternativa A (Actions + JSON + Pages) | Alternativa B (FastAPI em Render/Railway) |
|---|---|---|
| Scraping duas vezes ao dia | Job de ~1 min, grátis | Precisa de scheduler/cron do provedor |
| Persistência | Git: versionada, com histórico e auditoria | Exige disco/banco persistente (em geral pago; discos de planos gratuitos costumam ser efêmeros) |
| Consulta pelo navegador | Arquivos estáticos | API + serviço sempre no ar (planos gratuitos costumam “dormir” por inatividade) |
| Cálculos | Em JavaScript no navegador (testados contra o Python) | No servidor |
| Custo/manutenção | R$ 0; nada para manter ligado | Custo mensal e mais superfície de ataque |

Nada nesta aplicação exige processamento sob demanda no servidor: os dados mudam no máximo uma vez por mês por
índice e os cálculos são aritmética simples sobre a série. *(Os limites de planos gratuitos mudam; confira a
documentação dos provedores antes de decidir por B.)*

## Estrutura
```
src/indices/            núcleo Python
  coletores/{ipca,icti,ist}.py   um coletor independente por índice
  http.py               timeout, 3 tentativas, TLS verificado (exceção só IST, com aviso)
  validacao.py          bloqueia dados inválidos ANTES de gravar
  armazenamento.py      JSON atômico, upsert sem duplicar, revisões preservadas
  calculos.py           acumulado (Decimal) — referência dos testes
  logs.py               logs por índice e consolidado (JSONL + visões JSON)
  orquestrador.py       isolamento por índice, trava de concorrência, status
  cli.py                python -m indices ...
web/                    interface (HTML/CSS/JS sem dependências externas)
data/                   JSON dos índices, status e logs (commitados pelo robô)
.github/workflows/      atualizar.yml (09h/19h + manual + publicação) e testes.yml
tests/                  142 testes (pytest, Node, Chromium)
```

## Formato dos dados (`data/<índice>.json`)
`indice` (sigla, órgão, base, casas), `fonte`, `metodologia`, `atualizado_em` (só muda quando os **dados** mudam),
`ultima_competencia` e `serie[]` em ordem crescente, uma linha por competência:
`{"competencia":"2026-09","data_referencia":"2026-09-01","indice":7695.82,"var_mes":0.82,"var_12m":4.58,
"var_12m_origem":"fonte|calculada","primeira_coleta":"…","revisoes":[{"em","campo","de","para"}]}`.
Uma competência = uma linha (sem duplicidade); valores corrigidos pela fonte entram em `revisoes`.

## Como cada índice é coletado
* **IPCA** — API SIDRA/IBGE (tabela 1737, JSON). Contingência: o ZIP/XLS do FTP do IBGE (método original), lido em memória.
* **ICTI** — localiza no blog do Ipea a publicação “Índice de Custo da Tecnologia da Informação (ICTI) – mês de ano” mais recente e baixa o XLSX.
* **IST** — tabelas HTML da página da Anatel (não há API/arquivo estruturado).

Validações (qualquer erro → nada é gravado e o histórico anterior permanece): mínimo de competências, sem lacunas nem
duplicatas conflitantes, valores plausíveis, variação mensal coerente com os números-índice, base do índice inalterada,
fonte não regride, sem competência futura, revisões grandes (>0,5%) ou em massa bloqueadas; revisões pequenas viram aviso.

## Cálculo acumulado
Fator = índice(mês final) ÷ índice(mês anterior ao inicial) [padrão, igual ao original: meses de início a fim,
inclusive] ou ÷ índice(mês inicial) [modo “entre datas-base”, para reajustes contratuais]. Ver `docs/DIAGNOSTICO.md`
para as correções em relação ao original (arredondamento, período invertido, pré-Real etc.).

## Implantação (uma vez)
1. Crie um repositório no GitHub e envie este conteúdo (`git push`).
2. **Settings → Pages → Source: GitHub Actions.**
3. **Settings → Actions → General → Workflow permissions: Read and write.**
4. **Actions → “Atualizar índices” → Run workflow** (marque “simulação” na primeira vez para testar as três fontes
   sem gravar; depois rode sem marcar). O site fica em `https://<usuário>.github.io/<repositório>/`.
Não há segredos, tokens nem credenciais em nenhum arquivo.

## Operação
* **Agendamento**: cron UTC `12:00` e `22:00` = 09h e 19h em America/Maceio (UTC−3, sem horário de verão). O GitHub pode
  atrasar execuções agendadas; por isso há uma execução de **reserva** 45 min depois, que só atua se nada rodou nas últimas 2 h.
  O site mostra “Atualização atrasada” se a última execução tiver mais de 36 h.
* **Execução manual**: Actions → Atualizar índices → Run workflow (opcional: um índice só; modo simulação).
* **Falhas**: o job fica vermelho (o GitHub notifica por e-mail), o site mostra o erro em Registros e os dados antigos continuam.
* **Se uma fonte mudar de layout**: o log mostra qual etapa falhou; corrija só o arquivo do coletor correspondente.
* **Repositório público** ⇒ site e logs são públicos. Os logs são sanitizados (sem caminhos, tokens, IPs, stack traces) e os
  dados são públicos por natureza. Para restringir o acesso, hospede `site/` atrás de um provedor com controle de acesso.
* Execuções agendadas de repositórios públicos são pausadas após 60 dias sem atividade; como o robô faz commit a cada
  atualização, isso não deve ocorrer, e o aviso “Atualização atrasada” do site denuncia qualquer parada.

## Desenvolvimento
```bash
pip install -r requirements.txt pytest playwright && playwright install chromium
python -m pytest -q                              # PYTHONPATH=src já configurado no pyproject
PYTHONPATH=src python -m indices atualizar --dry-run
python scripts/build_site.py && python -m http.server -d site 8000
```
Carga inicial a partir dos arquivos do projeto original (já executada): `python -m indices semear --legado tests/fixtures`.
