// Executa web/js/calc.js contra os vetores gerados pela implementação de referência em Python.
import { readFileSync } from 'node:fs';
import { acumulado, ErroCalculo } from '../web/js/calc.js';

const dir = new URL('../data/', import.meta.url);
const docs = Object.fromEntries(['ipca', 'icti', 'ist'].map((c) => [c, JSON.parse(readFileSync(new URL(`${c}.json`, dir)))]));
const v = JSON.parse(readFileSync(new URL('./vetores_calculo.json', import.meta.url)));
let falhas = 0;
for (const k of v.casos) {
  const r = acumulado(docs[k.indice], k.inicio, k.fim, k.modo, k.valor);
  for (const campo of ['indice', 'modo', 'inicio', 'fim', 'meses', 'fator', 'percentual', 'valor_original', 'valor_corrigido']) {
    if (JSON.stringify(r[campo]) !== JSON.stringify(k.esperado[campo])) { falhas++; console.log('DIVERGE', k.indice, k.inicio, k.fim, campo, r[campo], k.esperado[campo]); }
  }
  if (r.avisos.length !== k.esperado.avisos.length) { falhas++; console.log('AVISOS', k.indice, r.avisos.length, k.esperado.avisos.length); }
}
for (const k of v.erros) {
  try { acumulado(docs[k.indice], k.inicio, k.fim, k.modo); falhas++; console.log('deveria falhar', k); }
  catch (e) { if (!(e instanceof ErroCalculo) || e.codigo !== k.codigo) { falhas++; console.log('codigo errado', k, e.codigo); } }
}
console.log(`${v.casos.length} casos + ${v.erros.length} erros verificados; ${falhas} divergência(s)`);
process.exit(falhas ? 1 : 0);
