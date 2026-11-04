// Cálculo de índices acumulados — espelho exato de src/indices/calculos.py (testado por paridade).
// fator = I(fim) / I(mês anterior ao início)  [inclusivo]   |   I(fim) / I(início)  [data_base]
export class ErroCalculo extends Error {
  constructor(codigo, mensagem) { super(mensagem); this.codigo = codigo; this.mensagem = mensagem; }
}
const ABREV = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
export const LIMITE_MOEDA_IPCA = '1994-07';

export const ordinal = (c) => { const [a, m] = c.split('-').map(Number); return a * 12 + m - 1; };
export const deOrdinal = (n) => `${String(Math.floor(n / 12)).padStart(4, '0')}-${String((n % 12) + 1).padStart(2, '0')}`;
export const somarMeses = (c, n) => deOrdinal(ordinal(c) + n);
export const rotulo = (c) => { const [a, m] = c.split('-').map(Number); return `${ABREV[m - 1]}/${a}`; };

export function parseMesAno(texto) {
  const t = (texto || '').trim();
  let m = /^(0?[1-9]|1[0-2])\/(\d{4})$/.exec(t);
  if (m) return `${m[2]}-${String(Number(m[1])).padStart(2, '0')}`;
  if (/^\d{4}-(0[1-9]|1[0-2])$/.test(t)) return t;
  throw new ErroCalculo('data_invalida', `Data inválida: "${texto}". Use o formato MM/AAAA (mês de 01 a 12).`);
}

export function parseValorBR(texto) {
  let t = (texto || '').trim().replace('R$', '').replace(/\s/g, '');
  if (!t) throw new ErroCalculo('valor_invalido', 'Valor vazio.');
  if (t.includes(',')) t = t.replace(/\./g, '').replace(',', '.');
  if (!/^\d+(\.\d+)?$/.test(t)) throw new ErroCalculo('valor_invalido', `Valor inválido: "${texto}". Exemplo: 1.000,00`);
  return Number(t);
}

// arredondamento comercial (meio para longe de zero), como ROUND_HALF_UP do Decimal
function arred(x, casas) {
  const f = 10 ** casas;
  const s = Math.sign(x);
  return (s * Math.round(Math.abs(x) * f * (1 + 1e-12))) / f;
}
const fmt = (x, casas) => arred(x, casas).toFixed(casas);

function indiceEm(porComp, doc, comp) {
  const e = porComp.get(comp);
  if (e) return { valor: e.indice, info: { competencia: comp, indice: e.indice, origem: 'serie' } };
  const base = doc.indice.base_anterior;
  if (base && base.competencia === comp && base.indice != null)
    return { valor: base.indice, info: { competencia: comp, indice: base.indice, origem: 'base' } };
  const p = doc.serie[0];
  if (comp === somarMeses(p.competencia, -1) && p.var_mes != null) {
    const v = p.indice / (1 + p.var_mes / 100);
    return { valor: v, info: { competencia: comp, indice: v, origem: 'reconstruido' } };
  }
  throw new ErroCalculo('fora_da_serie', `Não há número-índice para ${rotulo(comp)}.`);
}

export function acumulado(doc, inicio, fim, modo = 'inclusivo', valor = null) {
  if (!['inclusivo', 'data_base'].includes(modo)) throw new ErroCalculo('modo_invalido', `Modo de cálculo inválido: ${modo}`);
  const sigla = doc.indice.sigla;
  const porComp = new Map(doc.serie.map((e) => [e.competencia, e]));
  const { primeira_competencia: primeira, ultima_competencia: ultima } = doc;
  if (ordinal(inicio) > ordinal(fim)) throw new ErroCalculo('periodo_invertido', 'A data inicial é posterior à data final.');
  if (modo === 'data_base' && inicio === fim) throw new ErroCalculo('periodo_vazio', 'No modo data-base, as duas datas precisam ser diferentes.');
  for (const c of [inicio, fim]) {
    if (c > ultima) throw new ErroCalculo('nao_divulgado', `${sigla} de ${rotulo(c)} ainda não foi divulgado. Último mês disponível: ${rotulo(ultima)}.`);
  }
  if (inicio < primeira) throw new ErroCalculo('fora_da_serie', `${sigla} só está disponível a partir de ${rotulo(primeira)}.`);

  const denComp = modo === 'inclusivo' ? somarMeses(inicio, -1) : inicio;
  const num = indiceEm(porComp, doc, fim);
  const den = indiceEm(porComp, doc, denComp);
  const fator = num.valor / den.valor;
  const perc = (fator - 1) * 100;
  const avisos = [];
  if (den.info.origem === 'reconstruido')
    avisos.push(`O índice de ${rotulo(denComp)} não é publicado; foi reconstruído a partir da variação mensal de ${rotulo(inicio)} (precisão limitada a ±0,005 p.p.).`);
  let valorOriginal = null; let corrigido = null;
  if (valor !== null && valor !== '') {
    const v = typeof valor === 'number' ? valor : parseValorBR(valor);
    if (doc.indice.codigo === 'ipca' && inicio < LIMITE_MOEDA_IPCA) {
      avisos.push('Valor corrigido não calculado: para períodos que começam antes de jul/1994 a moeda era outra (cruzeiro real/URV). O percentual acumulado é válido; converta o valor para Real pela regra oficial antes de corrigi-lo.');
    } else { valorOriginal = v; corrigido = v * fator; }
  }
  return {
    indice: sigla, modo, inicio, fim, meses: ordinal(fim) - ordinal(denComp),
    fator: fmt(fator, 8), percentual: fmt(perc, 2),
    valor_original: valorOriginal === null ? null : fmt(valorOriginal, 2),
    valor_corrigido: corrigido === null ? null : fmt(corrigido, 2),
    avisos, numerador: num.info, denominador: den.info, metodologia: doc.metodologia.acumulado,
  };
}
