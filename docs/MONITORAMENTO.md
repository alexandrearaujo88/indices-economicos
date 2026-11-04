# Monitoramento e alertas

Três camadas, da mais simples à mais independente. As duas primeiras funcionam sem nenhuma configuração externa.

| Camada | Detecta | Como avisa | Depende do GitHub Actions? |
|---|---|---|---|
| 1. Alerta de execução (job `monitorar`) | falha de **um coletor** (IPCA, ICTI ou IST); erro do fluxo (coleta/gravação) ou da **publicação do site** | issue `[Alerta] …` com menção ao dono ⇒ e-mail | sim |
| 2. Vigia (`Vigiar atualizações`) | execução agendada que **não ocorreu** (2h15 após 09h e 19h de Maceió) | issue `[Alerta] Atualização automática não ocorreu` ⇒ e-mail | sim (outro agendamento) |
| 3. Sinal de vida externo (opcional) | o GitHub Actions **parou de executar** (agendamento desativado, indisponibilidade, repositório sem atividade) | e-mail do serviço externo | **não** |

O fluxo "Atualizar índices" fica **vermelho** quando um coletor falha; o job `monitorar` roda mesmo assim, registra o
problema e abre a issue. Um erro estrutural (instalação, gravação no repositório, publicação) gera o alerta `workflow`;
uma falha só de coletor gera apenas o alerta do coletor, sem duplicidade.

## Como os alertas evitam repetição
* Problema novo → abre **uma** issue (1 e-mail). Cada alerta traz índice, horário (Maceió), motivo resumido e links
  para a execução no GitHub Actions e para os Registros do site.
* Problema que continua → apenas **edita** a issue (edição não envia e-mail).
* Problema resolvido → comenta e **fecha** a issue (1 e-mail). No máximo dois e-mails por incidente.
* Uma execução ignorada (reserva) ou uma simulação não altera nenhum alerta.
* A ausência de competência nova **não é erro** e não gera alerta: o painel mostra “Sem novidades”.

## Configuração (uma vez, ~2 min)
1. **E-mail do GitHub**: em *Settings → Notifications*, mantenha o e-mail ativo para *Participating, @mentions and custom*
   (padrão). A menção na issue é o que garante o envio. Em *Actions*, marque também *Send notifications for failed
   workflows only* para receber o e-mail nativo de fluxos que falhem.
2. **Testar o canal**: *Actions → Vigiar atualizações → Run workflow → teste_de_alerta = abrir*. Você deve receber o
   e-mail “[Alerta] Teste do canal de notificações”. Depois rode de novo com *encerrar*.

## Camada 3 — sinal de vida externo (recomendada, gratuita)
O fluxo de atualização chama uma URL ao terminar. Se a chamada não chegar no horário esperado, o serviço externo avisa
por e-mail — inclusive quando o próprio GitHub Actions não executa nada.

Opção avaliada: [Healthchecks.io](https://healthchecks.io) (plano gratuito: 20 verificações; avisa por e-mail quando
uma verificação passa a “Down”). Passos:
1. Crie a conta e uma verificação com agendamento *Cron* `0 12,22 * * *` (UTC; equivale a 09h e 19h de Maceió) e
   *Grace time* de **3 horas** (cobre a reserva e atrasos do GitHub). Ative a integração de e-mail.
2. Copie a *ping URL* e salve-a em *GitHub → Settings → Secrets and variables → Actions → New repository secret* com o
   nome **`HEALTHCHECK_URL`**. Não cole essa URL em chats, issues ou arquivos: quem a conhece pode enviar sinais falsos.
3. Pronto: sem o segredo, o passo é ignorado; com ele, cada execução envia sucesso (ou `/fail` se o fluxo falhar).

Alternativa gratuita de monitoramento do **site** (disponibilidade): qualquer monitor HTTP (por exemplo UptimeRobot)
apontando para `…/data/status.json`. Ele detecta o site fora do ar, mas não a ausência de atualização.

## Limites conhecidos
* As camadas 1 e 2 compartilham a infraestrutura do GitHub; só a camada 3 cobre uma parada total do Actions.
* O GitHub informa que agendamentos de repositórios públicos são desativados após 60 dias sem “atividade” e não define
  o termo com precisão. Como o robô grava logs a cada coleta, espera-se que isso não ocorra; o vigia e o sinal externo
  existem justamente para o caso de ocorrer. Para reativar: *Actions → fluxo → Enable workflow*.
* Notificações de execuções agendadas do GitHub vão para quem criou o workflow ou alterou o cron. Por isso o canal
  principal é a issue com menção, que não depende dessa regra.
