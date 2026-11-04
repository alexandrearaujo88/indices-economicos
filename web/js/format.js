// Formatação pt-BR (números, percentuais, datas no fuso de Maceió).
const FUSO = 'America/Maceio';
const nf = {};
export function num(x, casas = 2) {
  if (x === null || x === undefined || Number.isNaN(x)) return '—';
  nf[casas] ??= new Intl.NumberFormat('pt-BR', { minimumFractionDigits: casas, maximumFractionDigits: casas });
  return nf[casas].format(x);
}
export const pct = (x, casas = 2) => (x === null || x === undefined ? '—' : `${num(x, casas)}%`);
export const moeda = (s) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(Number(s));
export const MESES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro'];
export const mesNome = (c) => MESES[Number(c.slice(5)) - 1];
export const compLonga = (c) => `${mesNome(c)} de ${c.slice(0, 4)}`;
const dh = new Intl.DateTimeFormat('pt-BR', { timeZone: FUSO, day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
export function dataHora(iso) {
  if (!iso) return '—';
  return dh.format(new Date(iso)).replace(',', '');
}
export function relativo(iso, agora = Date.now()) {
  if (!iso) return '';
  const min = Math.round((agora - new Date(iso).getTime()) / 60000);
  if (min < 1) return 'agora há pouco';
  if (min < 60) return `há ${min} min`;
  const h = Math.round(min / 60);
  if (h < 48) return `há ${h} h`;
  return `há ${Math.round(h / 24)} dias`;
}
// Próxima execução: 09:00 e 19:00 em America/Maceio (UTC−3, sem horário de verão) = 12:00 e 22:00 UTC.
export function proximaExecucao(agora = new Date()) {
  const base = Date.UTC(agora.getUTCFullYear(), agora.getUTCMonth(), agora.getUTCDate());
  const cand = [12, 22, 36].map((h) => base + h * 3600000).find((t) => t > agora.getTime());
  return new Date(cand).toISOString();
}
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
