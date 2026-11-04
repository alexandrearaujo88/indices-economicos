import { acumulado, ErroCalculo, parseMesAno, rotulo, somarMeses } from './calc.js';
import { num, pct, moeda, mesNome, compLonga, dataHora, relativo, proximaExecucao, esc } from './format.js';

const CODS = ['ipca', 'icti', 'ist'];
const ST = { sucesso: ['Sucesso', '✓'], aviso: ['Aviso', '▲'], erro: ['Erro', '✕'] };
const S = { status: null, docs: {}, cons: null, logsInd: {}, falhas: [] };
const $ = (s, r = document) => r.querySelector(s);
const main = $('#principal');

async function json(url) {
  const r = await fetch(url, { cache: 'no-cache' });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}
async function carregar() {
  const [status, ...docs] = await Promise.allSettled([json('data/status.json'), ...CODS.map((c) => json(`data/${c}.json`))]);
  S.status = status.status === 'fulfilled' ? status.value : null;
  CODS.forEach((c, i) => { if (docs[i].status === 'fulfilled') S.docs[c] = docs[i].value; else S.falhas.push(c); });
  try { S.cons = (await json('data/logs/consolidado.json')).registros; } catch { S.cons = []; }
}
const chip = (r) => { const [t, i] = ST[r] || ['—', '•']; return `<span class="chip" data-s="${esc(r)}"><span aria-hidden="true">${i}</span>${t}</span>`; };
const sinal = (x, c = 2) => (x == null ? '—' : `<span class="${x < 0 ? 'neg' : ''}">${pct(x, c)}</span>`);
const ultima = (c) => S.docs[c].serie[S.docs[c].serie.length - 1];

// ------------------------------------------------------------------ estado geral
// Quatro situações distintas (não confundir): coleta validada = a fonte foi consultada e os dados passaram na
// validação; "atualizado" = a coleta incorporou novas competências/revisões; "sem novidades" = a coleta
// funcionou e nada mudou (normal: cada índice tem o seu calendário de divulgação); "falha" = erro.
const SIT = { atualizado: ['Dados atualizados', '↑', 'sucesso'], sem_novidades: ['Sem novidades', '✓', 'neutro'], falha: ['Falha', '✕', 'erro'] };
const TIPO_ALT = { carga_inicial: 'carga inicial', novas_competencias: 'novas competências', revisao: 'revisão de valores' };
const chipSit = (k) => { const [t, i, s] = SIT[k] || ['—', '•', 'neutro']; return `<span class="chip" data-s="${s}"><span aria-hidden="true">${i}</span>${t}</span>`; };
function situacao(c) {
  const i = S.status?.indices?.[c]; if (!i) return null;
  if (i.situacao) return i.situacao;
  if (i.resultado === 'erro') return 'falha';                         // status gerado por versão anterior
  return /^Sem novidades/.test(i.mensagem || '') ? 'sem_novidades' : 'atualizado';
}
function resumoMudanca(c) {
  const i = S.status.indices[c]; const col = i.ultima_coleta || {}; const partes = [];
  if (i.ultima_alteracao_tipo === 'carga_inicial' && i.situacao === 'atualizado') partes.push('carga inicial dos dados');
  else {
    const n = (col.novas_competencias || []).length; const r = (col.competencias_revisadas || []).length;
    if (n) partes.push(`${n} ${n === 1 ? 'nova competência' : 'novas competências'} (até ${rotulo(col.novas_competencias.at(-1))})`);
    if (r) partes.push(`${r} ${r === 1 ? 'competência revisada' : 'competências revisadas'}`);
  }
  return partes.join(' e ') || 'dados atualizados';
}
function alteracao(c) {
  const i = S.status?.indices?.[c]; const quando = i?.ultima_alteracao_dados || S.docs[c]?.atualizado_em;
  if (!quando) return 'não registrada';
  const t = TIPO_ALT[i?.ultima_alteracao_tipo];
  return `${dataHora(quando)}${t ? ` · ${t}` : ''}`;
}
function saude() {
  const st = S.status;
  if (!st) return { s: 'aviso', t: 'Sem informação de execução', d: 'O arquivo de status ainda não existe: nenhuma coleta automática foi registrada.' };
  const fim = st.execucao.fim; const idade = (Date.now() - new Date(fim).getTime()) / 3600000;
  const falhas = CODS.filter((c) => situacao(c) === 'falha');
  const prox = `Próxima coleta: ${dataHora(proximaExecucao())}.`;
  if (idade > 36) return { s: 'erro', t: 'Coleta atrasada', d: `A última coleta foi ${relativo(fim)}. As coletas automáticas deveriam ocorrer às 09h e às 19h. Os dados exibidos são os últimos válidos.` };
  if (falhas.length) { const l = falhas.map((c) => c.toUpperCase()).join(', '); return { s: 'erro', t: `Falha em ${l}`, d: `A última coleta de ${l} falhou. Os dados anteriores foram preservados. ${prox} Detalhes em Registros.` }; }
  if (CODS.some((c) => st.indices[c]?.resultado === 'aviso')) return { s: 'aviso', t: 'Coleta validada, com avisos', d: 'A última coleta terminou com avisos (por exemplo, revisão de valores ou fonte de contingência). Veja em Registros.' };
  const mudou = CODS.filter((c) => situacao(c) === 'atualizado');
  if (mudou.length) return { s: 'sucesso', t: 'Dados atualizados', d: `${mudou.map((c) => `${c.toUpperCase()}: ${resumoMudanca(c)}`).join(' · ')}. Coleta validada ${relativo(fim)} (${dataHora(fim)}). ${prox}` };
  return { s: 'sucesso', t: 'Coleta validada, sem novidades', d: `As fontes foram consultadas e os dados validados ${relativo(fim)} (${dataHora(fim)}); nenhuma competência nova ou revisão. ${prox}` };
}
function pintarEstado() {
  const h = saude();
  const b = $('#estado-sistema'); b.dataset.s = h.s; $('#estado-texto').textContent = h.t;
  const f = $('#estado-detalhe'); f.dataset.s = h.s; f.textContent = h.d;
  f.hidden = h.s === 'sucesso' && !f.dataset.aberto;
  b.onclick = () => { f.hidden = !f.hidden; f.dataset.aberto = f.hidden ? '' : '1'; };
}

// ------------------------------------------------------------------ painel
function atrasoDados(c) {
  const u = S.docs[c].ultima_competencia; const fimMes = new Date(Date.UTC(+u.slice(0, 4), +u.slice(5), 1));
  return (Date.now() - fimMes.getTime()) / 86400000;
}
function viewPainel() {
  const cards = CODS.map((c) => {
    const d = S.docs[c]; if (!d) return `<div class="painel"><h3>${c.toUpperCase()}</h3><p class="vazio">Dados indisponíveis.</p></div>`;
    const u = ultima(c); const info = S.status?.indices?.[c]; const casas = d.indice.casas_indice;
    const ok = info?.ultima_coleta_ok; const sit = situacao(c);
    const tardio = atrasoDados(c) > 75;
    const detalhe = sit === 'atualizado' ? `<br>Nesta coleta: ${esc(resumoMudanca(c))}` :
      sit === 'falha' ? `<br><strong>Falha em ${dataHora(info.ultima_falha?.em || info.ultima_tentativa)}:</strong> ${esc(info.ultima_falha?.mensagem || info.mensagem)}` : '';
    return `<a class="painel ind" href="#/indices/${c}" aria-label="Ver série do ${d.indice.sigla}">
      <div class="ind-topo"><span class="ind-sigla">${d.indice.sigla}</span>${sit ? chipSit(sit) : chip('neutro')}</div>
      <span class="ind-nome">${esc(d.indice.nome)} · ${esc(d.indice.orgao)}</span>
      <div class="grande">${sinal(u.var_12m)}</div><div class="rot">variação em 12 meses até ${rotulo(u.competencia)}</div>
      <div class="par"><div><span class="rot">Mês</span><b>${sinal(u.var_mes)}</b></div><div><span class="rot">Índice</span><b>${num(u.indice, casas)}</b></div></div>
      <div class="meta">Competência mais recente: ${compLonga(u.competencia)}${tardio ? '<br>Próxima competência ainda não divulgada pela fonte.' : ''}<br>Última coleta bem-sucedida: ${ok ? `${dataHora(ok)} (${relativo(ok)})` : '—'}<br>Última alteração nos dados: ${alteracao(c)}${detalhe}${info?.resultado === 'aviso' ? '<br>Coleta concluída com avisos (veja em Registros).' : ''}</div>
    </a>`;
  }).join('');
  const rec = (S.cons || []).filter((e) => e.indice !== 'sistema').slice(0, 6);
  return `<h2>Painel</h2><p class="sub">Situação mais recente dos três índices. Clique em um índice para ver a série completa.</p>
  <div class="grade3">${cards}</div>
  <section class="sec"><h2>Últimos eventos</h2><p class="sub">As seis execuções mais recentes. <a href="#/logs">Ver todos os registros</a>.</p>${tabelaLogs(rec, false)}</section>`;
}

// ------------------------------------------------------------------ calculadora
function viewCalc() {
  const hoje = Object.values(S.docs).map((d) => d.ultima_competencia).sort()[0];
  return `<h2>Calculadora de índice acumulado</h2><p class="sub">Variação acumulada de um índice entre dois meses, com correção opcional de um valor. O cálculo usa a razão entre os números-índice publicados.</p>
  <form id="f-calc" class="painel" novalidate>
    <div class="form">
      <div><label for="c-ind">Índice</label><select id="c-ind">${CODS.map((c) => `<option value="${c}">${S.docs[c].indice.sigla} — ${esc(S.docs[c].indice.nome)}</option>`).join('')}</select></div>
      <div><label for="c-ini">Mês inicial</label><input id="c-ini" inputmode="numeric" placeholder="MM/AAAA" maxlength="7" autocomplete="off" required><p class="dica" id="d-ini"></p></div>
      <div><label for="c-fim">Mês final</label><input id="c-fim" inputmode="numeric" placeholder="MM/AAAA" maxlength="7" autocomplete="off" required><p class="dica" id="d-fim"></p></div>
      <div><label for="c-val">Valor a corrigir (opcional)</label><input id="c-val" inputmode="decimal" placeholder="1.000,00" autocomplete="off"></div>
    </div>
    <fieldset style="margin-top:14px"><legend>Meses considerados</legend>
      <label class="radio"><input type="radio" name="modo" value="inclusivo" checked><span>Do mês inicial ao final, inclusive<small>Soma a variação de todos os meses do período. 01/2024 a 12/2024 = variação de 2024.</small></span></label>
      <label class="radio"><input type="radio" name="modo" value="data_base"><span>Entre datas-base (reajuste contratual)<small>Índice do mês final ÷ índice do mês inicial. 01/2024 a 01/2025 = 12 meses; não inclui a variação do mês inicial.</small></span></label>
    </fieldset>
    <div style="margin-top:14px"><button class="btn" type="submit">Calcular</button> <button class="btn sec2" type="reset">Limpar</button></div>
  </form>
  <div id="c-res" aria-live="polite"></div>
  <p class="dica" style="margin-top:12px">Mês mais recente disponível para os três índices: ${rotulo(hoje)}. Cada índice tem o seu próprio último mês.</p>`;
}
function bindCalc() {
  const ind = $('#c-ind');
  const mascara = (el) => el.addEventListener('input', () => {
    let v = el.value.replace(/\D/g, '').slice(0, 6); if (v.length > 2) v = `${v.slice(0, 2)}/${v.slice(2)}`; el.value = v;
  });
  ['#c-ini', '#c-fim'].forEach((s) => mascara($(s)));
  const dicas = () => { const d = S.docs[ind.value]; $('#d-ini').textContent = `Disponível desde ${rotulo(d.primeira_competencia)}`; $('#d-fim').textContent = `Até ${rotulo(d.ultima_competencia)}`; };
  ind.addEventListener('change', dicas); dicas();
  $('#f-calc').addEventListener('reset', () => { $('#c-res').innerHTML = ''; setTimeout(dicas); });
  $('#f-calc').addEventListener('submit', (ev) => {
    ev.preventDefault(); const out = $('#c-res');
    try {
      const modo = document.querySelector('input[name=modo]:checked').value;
      const r = acumulado(S.docs[ind.value], parseMesAno($('#c-ini').value), parseMesAno($('#c-fim').value), modo, $('#c-val').value.trim() || null);
      const d = S.docs[ind.value];
      const nm = Number(r.percentual);
      out.innerHTML = `<div class="res"><div class="rot">${r.indice} acumulado de ${rotulo(r.inicio)} a ${rotulo(r.fim)} (${r.meses} ${r.meses === 1 ? 'mês' : 'meses'} de variação)</div>
        <div class="grande ${nm < 0 ? 'neg' : ''}">${pct(nm)}</div>
        <dl><dt>Fator de correção</dt><dd>${num(Number(r.fator), 8)}</dd>
        ${r.valor_corrigido ? `<dt>Valor original</dt><dd>${moeda(r.valor_original)}</dd><dt>Valor corrigido</dt><dd><strong>${moeda(r.valor_corrigido)}</strong></dd>` : ''}
        <dt>Índice final</dt><dd>${num(r.numerador.indice, d.indice.casas_indice)} (${rotulo(r.numerador.competencia)})</dd>
        <dt>Índice de partida</dt><dd>${num(r.denominador.indice, d.indice.casas_indice)} (${rotulo(r.denominador.competencia)}${r.denominador.origem === 'base' ? ', base do índice' : ''})</dd>
        <dt>Metodologia</dt><dd>${esc(r.metodologia)} Modo: ${modo === 'inclusivo' ? 'meses do início ao fim, inclusive' : 'entre datas-base'}. Fonte: ${esc(d.indice.orgao)}.</dd></dl>
        ${r.avisos.length ? `<ul>${r.avisos.map((a) => `<li>${esc(a)}</li>`).join('')}</ul>` : ''}</div>`;
    } catch (e) {
      if (!(e instanceof ErroCalculo)) throw e;
      out.innerHTML = `<div class="res" data-s="erro" role="alert"><strong>Não foi possível calcular.</strong> ${esc(e.mensagem)}</div>`;
    }
  });
}

// ------------------------------------------------------------------ séries
function grafico(d, anos) {
  const pts = d.serie.filter((e) => e.var_12m != null);
  const sel = anos ? pts.slice(-anos * 12) : pts;
  const W = 760, H = 280, m = { l: 44, r: 12, t: 12, b: 26 };
  const ys = sel.map((e) => e.var_12m);
  const hi = Math.ceil(Math.max(...ys, 0) / 2) * 2, lo = Math.floor(Math.min(...ys, 0) / 2) * 2;
  const x = (i) => m.l + (i * (W - m.l - m.r)) / Math.max(sel.length - 1, 1);
  const y = (v) => m.t + ((hi - v) * (H - m.t - m.b)) / (hi - lo || 1);
  const passo = Math.max(2, Math.ceil((hi - lo) / 5 / 2) * 2);
  let grade = ''; for (let v = lo; v <= hi; v += passo) grade += `<line class="${v === 0 ? 'zero' : 'grade'}" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/><text class="eixo" x="${m.l - 6}" y="${y(v) + 4}" text-anchor="end">${num(v, 0)}%</text>`;
  const anosEix = sel.filter((e) => e.competencia.endsWith('-01')); const salto = Math.ceil(anosEix.length / 8);
  const eixoX = anosEix.filter((_, i) => i % salto === 0).map((e) => `<text class="eixo" x="${x(sel.indexOf(e))}" y="${H - 6}" text-anchor="middle">${e.competencia.slice(0, 4)}</text>`).join('');
  const linha = sel.map((e, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(e.var_12m).toFixed(1)}`).join('');
  const area = `${linha}L${x(sel.length - 1).toFixed(1)},${y(Math.max(lo, 0)).toFixed(1)}L${x(0).toFixed(1)},${y(Math.max(lo, 0)).toFixed(1)}Z`;
  return { sel, W, H, x, svg: `<svg class="grafico" viewBox="0 0 ${W} ${H}" role="img" aria-label="Variação em 12 meses do ${d.indice.sigla}, de ${rotulo(sel[0].competencia)} a ${rotulo(sel.at(-1).competencia)}. Os valores estão na tabela abaixo.">${grade}${eixoX}<path class="area" d="${area}"/><path class="linha" d="${linha}"/><line class="cruz" id="cruz" y1="${m.t}" y2="${H - m.b}" visibility="hidden"/><circle class="ponto" id="pt" r="5" visibility="hidden"/></svg>` };
}
let serieEstado = { cod: 'ipca', anos: 10, ano: '' };
function viewSeries(cod) {
  if (CODS.includes(cod)) serieEstado.cod = cod;
  const c = serieEstado.cod; const d = S.docs[c]; if (!d) return '<p class="vazio">Dados indisponíveis.</p>';
  const casas = d.indice.casas_indice; const info = S.status?.indices?.[c];
  const anos = [...new Set(d.serie.map((e) => e.competencia.slice(0, 4)))].reverse();
  const linhas = [...d.serie].reverse().filter((e) => !serieEstado.ano || e.competencia.startsWith(serieEstado.ano));
  return `<h2>Séries históricas</h2><p class="sub">${esc(d.indice.nome)} (${esc(d.indice.orgao)}). ${esc(d.indice.natureza)}</p>
  <div class="barra"><div class="seg" role="group" aria-label="Índice">${CODS.map((x) => `<button type="button" data-cod="${x}" aria-pressed="${x === c}">${S.docs[x].indice.sigla}</button>`).join('')}</div>
    <div class="seg" role="group" aria-label="Período do gráfico">${[[5, '5 anos'], [10, '10 anos'], [0, 'Tudo']].map(([a, t]) => `<button type="button" data-anos="${a}" aria-pressed="${serieEstado.anos === a}">${t}</button>`).join('')}</div></div>
  <div class="painel"><h3 style="font-size:1rem;margin-bottom:8px">Variação em 12 meses (%)</h3><div class="gwrap" id="gw">${grafico(d, serieEstado.anos).svg}<div class="tip" id="tip" hidden></div></div>
    <p class="meta">Competência mais recente: ${compLonga(d.ultima_competencia)} · Base: ${esc(d.indice.base)} · Fonte: <a href="${esc(d.fonte.url_oficial)}" rel="noopener">${esc(d.fonte.nome)}</a> · Última alteração nos dados: ${alteracao(c)}${info?.ultima_coleta_ok ? ` · Última coleta bem-sucedida: ${dataHora(info.ultima_coleta_ok)}` : ''}</p></div>
  <section class="sec"><div class="barra"><div><label for="f-ano">Ano</label><select id="f-ano"><option value="">Todos (${d.serie.length} meses)</option>${anos.map((a) => `<option ${a === serieEstado.ano ? 'selected' : ''}>${a}</option>`).join('')}</select></div><button class="btn sec2" type="button" id="csv">Baixar CSV</button></div>
  <div class="tabela-w" tabindex="0" role="region" aria-label="Tabela de valores do ${d.indice.sigla}"><table><caption class="sr" style="position:absolute;left:-999px">Valores do ${d.indice.sigla}, do mais recente ao mais antigo</caption><thead><tr><th scope="col">Competência</th><th scope="col">Índice</th><th scope="col">Var. no mês</th><th scope="col">Var. em 12 meses</th></tr></thead><tbody>
  ${linhas.map((e) => `<tr><td>${mesNome(e.competencia).slice(0, 3)}/${e.competencia.slice(0, 4)}${e.revisoes ? `<span class="rev" title="${esc(e.revisoes.map((r) => `${dataHora(r.em)}: ${r.campo} ${r.de} → ${r.para}`).join('; '))}">revisado</span>` : ''}</td><td>${num(e.indice, casas)}</td><td>${sinal(e.var_mes)}</td><td>${e.var_12m == null ? '—' : sinal(e.var_12m)}${e.var_12m_origem === 'calculada' ? '<sup title="Calculada a partir dos números-índice; a fonte não publica este valor"> c</sup>' : ''}</td></tr>`).join('')}
  </tbody></table></div><p class="meta">“c”: variação calculada a partir dos números-índice (a fonte não a publica). “revisado”: valor já publicado foi corrigido pela fonte; passe o mouse para ver o histórico.</p></section>`;
}
function bindSeries() {
  const c = serieEstado.cod; const d = S.docs[c];
  document.querySelectorAll('[data-cod]').forEach((b) => b.addEventListener('click', () => { location.hash = `#/indices/${b.dataset.cod}`; }));
  document.querySelectorAll('[data-anos]').forEach((b) => b.addEventListener('click', () => { serieEstado.anos = Number(b.dataset.anos); render(); }));
  $('#f-ano').addEventListener('change', (e) => { serieEstado.ano = e.target.value; render(); $('#f-ano').focus(); });
  $('#csv').addEventListener('click', () => {
    const h = 'competencia;indice;var_mes;var_12m\n'; const f = (x) => (x == null ? '' : String(x).replace('.', ','));
    const t = h + d.serie.map((e) => [e.competencia, f(e.indice), f(e.var_mes), f(e.var_12m)].join(';')).join('\n');
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob(['﻿' + t], { type: 'text/csv' })); a.download = `${c}.csv`; a.click();
  });
  const g = grafico(d, serieEstado.anos); const svg = $('#gw svg'); const tip = $('#tip');
  const mover = (ev) => {
    const r = svg.getBoundingClientRect(); const px = ((ev.clientX - r.left) / r.width) * g.W;
    let i = Math.round(((px - 44) / (g.W - 56)) * (g.sel.length - 1)); i = Math.max(0, Math.min(g.sel.length - 1, i));
    const e = g.sel[i]; const cx = g.x(i);
    const vals = g.sel.map((s) => s.var_12m); const hi = Math.ceil(Math.max(...vals, 0) / 2) * 2, lo = Math.floor(Math.min(...vals, 0) / 2) * 2;
    const cy = 12 + ((hi - e.var_12m) * (280 - 12 - 26)) / (hi - lo || 1);
    $('#cruz').setAttribute('x1', cx); $('#cruz').setAttribute('x2', cx); $('#cruz').setAttribute('visibility', 'visible');
    $('#pt').setAttribute('cx', cx); $('#pt').setAttribute('cy', cy); $('#pt').setAttribute('visibility', 'visible');
    tip.hidden = false; tip.textContent = `${rotulo(e.competencia)}: ${pct(e.var_12m)}`; tip.style.left = `${(cx / g.W) * r.width}px`; tip.style.top = `${(cy / 280) * r.height}px`;
  };
  svg.addEventListener('pointermove', mover); svg.addEventListener('pointerdown', mover);
  svg.addEventListener('pointerleave', () => { tip.hidden = true; $('#cruz').setAttribute('visibility', 'hidden'); $('#pt').setAttribute('visibility', 'hidden'); });
}

// ------------------------------------------------------------------ logs
const DUR = (ms) => (ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1).replace('.', ',')} s`);
function tabelaLogs(regs, detalhe = true) {
  if (!regs.length) return '<p class="vazio">Nenhum registro ainda. A primeira execução automática criará os registros.</p>';
  return `<div class="tabela-w"><table><thead><tr><th scope="col">Data e hora</th><th scope="col" class="esq">Índice</th><th scope="col" class="esq hide-sm">Operação</th><th scope="col" class="esq">Resultado</th><th scope="col" class="esq">Mensagem</th><th scope="col" class="hide-sm">Duração</th>${detalhe ? '<th scope="col"><span class="sr" style="position:absolute;left:-999px">Detalhes</span></th>' : ''}</tr></thead><tbody>
  ${regs.map((e) => `<tr data-id="${esc(e.id)}"><td>${dataHora(e.inicio)}</td><td class="esq">${e.indice === 'sistema' ? 'Sistema' : e.indice.toUpperCase()}</td><td class="esq hide-sm">${esc(e.operacao)}<br><span class="meta">${esc(e.gatilho)}</span></td><td class="esq">${chip(e.resultado)}</td><td class="esq">${esc(e.mensagem)}</td><td class="hide-sm">${DUR(e.duracao_ms)}</td>${detalhe ? `<td><button class="tg" type="button" aria-expanded="false">Detalhes</button></td>` : ''}</tr>`).join('')}</tbody></table></div>`;
}
let logEstado = { aba: 'consolidado', res: '', ind: '' };
async function viewLogs(aba) {
  if (aba) logEstado.aba = aba;
  const a = logEstado.aba; let regs;
  if (a === 'consolidado') regs = S.cons || [];
  else { if (!S.logsInd[a]) { try { S.logsInd[a] = (await json(`data/logs/${a}.json`)).registros; } catch { S.logsInd[a] = []; } } regs = S.logsInd[a]; }
  const f = regs.filter((e) => (!logEstado.res || e.resultado === logEstado.res) && (!logEstado.ind || e.indice === logEstado.ind));
  const abas = [['consolidado', 'Consolidado'], ...CODS.map((c) => [c, c.toUpperCase()])];
  return `<h2>Registros de execução</h2><p class="sub">Do mais recente para o mais antigo. Cada linha é uma atualização de um índice; as etapas ficam em “Detalhes”.</p>
  <div class="barra"><div class="seg" role="group" aria-label="Registro">${abas.map(([k, t]) => `<button type="button" data-aba-log="${k}" aria-pressed="${k === a}">${t}</button>`).join('')}</div>
   <div><label for="l-res">Resultado</label><select id="l-res"><option value="">Todos</option>${['sucesso', 'aviso', 'erro'].map((r) => `<option value="${r}" ${logEstado.res === r ? 'selected' : ''}>${ST[r][0]}</option>`).join('')}</select></div>
   ${a === 'consolidado' ? `<div><label for="l-ind">Índice</label><select id="l-ind"><option value="">Todos</option>${[...CODS, 'sistema'].map((c) => `<option value="${c}" ${logEstado.ind === c ? 'selected' : ''}>${c === 'sistema' ? 'Sistema' : c.toUpperCase()}</option>`).join('')}</select></div>` : ''}</div>
  ${tabelaLogs(f)}<p class="meta">${f.length} de ${regs.length} registros exibidos (as últimas ${a === 'consolidado' ? 500 : 300} execuções; o histórico completo fica arquivado no repositório).</p>`;
}
function bindLogs() {
  document.querySelectorAll('[data-aba-log]').forEach((b) => b.addEventListener('click', () => { location.hash = `#/logs/${b.dataset.abaLog}`; }));
  $('#l-res')?.addEventListener('change', (e) => { logEstado.res = e.target.value; render(); });
  $('#l-ind')?.addEventListener('change', (e) => { logEstado.ind = e.target.value; render(); });
  document.querySelectorAll('button.tg').forEach((b) => b.addEventListener('click', async () => {
    const tr = b.closest('tr'); const prox = tr.nextElementSibling;
    if (prox?.classList.contains('det')) { prox.remove(); b.setAttribute('aria-expanded', 'false'); return; }
    const id = tr.dataset.id; const cod = id.split('-')[1] ? tr.children[1].textContent.toLowerCase() : '';
    const arq = cod === 'sistema' ? 'sistema' : cod;
    if (!S.logsInd[arq]) { try { S.logsInd[arq] = (await json(`data/logs/${arq}.json`)).registros; } catch { S.logsInd[arq] = []; } }
    const e = S.logsInd[arq].find((x) => x.id === id);
    const novo = document.createElement('tr'); novo.className = 'det';
    novo.innerHTML = `<td colspan="7">${e ? `<ol>${e.etapas.map((s) => `<li class="n-${s.nivel}"><time>${dataHora(s.em).slice(-5)}</time>${esc(s.msg)}</li>`).join('')}</ol>${e.resumo?.novos?.length ? `<p class="meta">Competências novas: ${e.resumo.novos.map(rotulo).join(', ')}</p>` : ''}${e.resumo?.revisoes?.length ? `<p class="meta">Revisões: ${e.resumo.revisoes.map((r) => `${rotulo(r.competencia)} ${r.campo} ${r.de} → ${r.para}`).join('; ')}</p>` : ''}` : 'Detalhes não disponíveis (registro mais antigo que o limite exibido).'}</td>`;
    tr.after(novo); b.setAttribute('aria-expanded', 'true');
  }));
}

// ------------------------------------------------------------------ sobre
function viewSobre() {
  const blocos = CODS.map((c) => { const d = S.docs[c]; return `<div class="painel fonte-bloco"><h3>${d.indice.sigla} — ${esc(d.indice.nome)}</h3><p>${esc(d.indice.natureza)}</p>
    <dl><dt>Órgão</dt><dd>${esc(d.indice.orgao)}</dd><dt>Base</dt><dd>${esc(d.indice.base)}</dd><dt>Fonte</dt><dd><a href="${esc(d.fonte.url_oficial)}" rel="noopener">${esc(d.fonte.nome)}</a></dd><dt>Coleta</dt><dd>${esc(d.fonte.metodo_coleta)}</dd><dt>Metodologia</dt><dd><a href="${esc(d.metodologia.url)}" rel="noopener">Documentação oficial</a></dd><dt>Acumulado</dt><dd>${esc(d.metodologia.acumulado)}</dd></dl>
    ${d.metodologia.observacoes.length ? `<ul>${d.metodologia.observacoes.map((o) => `<li>${esc(o)}</li>`).join('')}</ul>` : ''}</div>`; }).join('');
  return `<div class="sobre"><h2>Fontes e método</h2><p class="sub">De onde vêm os números e como o acumulado é calculado.</p>${blocos}
  <h3>Atualização</h3><p>A coleta roda sozinha às 09h e às 19h (horário de Maceió, UTC−3), na nuvem, sem depender de nenhum computador. Se uma fonte falhar ou entregar dados que não passam na validação, os valores anteriores são mantidos e o problema aparece em Registros. Valores já publicados que a fonte corrigir ficam marcados como “revisado”.</p><h3>Como ler a situação de cada índice</h3><ul><li><strong>Dados atualizados</strong>: a última coleta incorporou novas competências ou revisões.</li><li><strong>Sem novidades</strong>: a coleta funcionou e os dados validados são iguais aos já armazenados. É o normal na maior parte do mês, pois cada índice tem o seu calendário de divulgação.</li><li><strong>Falha</strong>: houve erro na coleta ou a validação reprovou os dados; os valores anteriores foram preservados.</li></ul><p>“Última coleta bem-sucedida” é quando a fonte foi consultada e validada pela última vez; “última alteração nos dados” é quando o arquivo do índice mudou de fato.</p>
  <h3>Execução manual</h3><p>Quem tem acesso ao repositório pode disparar uma atualização na aba Actions do GitHub (fluxo “Atualizar índices”, botão Run workflow).</p>
  <h3>Como o acumulado é calculado</h3><p>Fator = índice do mês final ÷ índice do mês anterior ao inicial (modo “inclusivo”) ou ÷ índice do mês inicial (modo “entre datas-base”). O percentual é o fator menos 1. Não se compõem variações mensais arredondadas, o que evita acumular erro de arredondamento.</p></div>`;
}

// ------------------------------------------------------------------ roteador
async function render() {
  const [, rota = 'painel', arg] = location.hash.split('/');
  document.querySelectorAll('.abas a').forEach((a) => (a.dataset.aba === rota ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current')));
  if (S.falhas.length === CODS.length) { main.innerHTML = '<p class="vazio" role="alert">Não foi possível carregar os dados. Tente novamente em instantes.</p>'; return; }
  if (rota === 'painel' || !['calculadora', 'indices', 'logs', 'sobre'].includes(rota)) main.innerHTML = viewPainel();
  else if (rota === 'calculadora') { main.innerHTML = viewCalc(); bindCalc(); }
  else if (rota === 'indices') { main.innerHTML = viewSeries(arg); bindSeries(); }
  else if (rota === 'logs') { main.innerHTML = await viewLogs(arg); bindLogs(); }
  else main.innerHTML = viewSobre();
  document.title = `${{ painel: 'Painel', calculadora: 'Calculadora', indices: 'Séries', logs: 'Registros', sobre: 'Fontes e método' }[rota] || 'Painel'} — Índices DAGGTIC`;
}
window.addEventListener('hashchange', () => { render(); window.scrollTo(0, 0); });
carregar().then(() => { pintarEstado(); render(); setInterval(pintarEstado, 60000); });
