// Summary ("Synthèse"): bottles per type, drinking phases, priorities, storages fill.
import { api, canWrite, clear, emptyState, fmtPrice, fmtYear, h, phaseBadge, state, t, typeDot, typeLabel, windowText } from '../core.js';

const PHASES = ['keep', 'drink', 'peak', 'decline', 'unknown'];

export async function renderSummary(_m, _q, el) {
  const s = await api('/summary');
  const head = h('div.page-head', h('h1', t('summary.title')));
  if (!s.total) {
    clear(el, head, emptyState(t('summary.empty'), canWrite()
      ? h('div.row-actions', h('a.btn.primary', { href: '#/wine/new' }, t('nav.add')),
        h('a.btn', { href: '#/settings/import' }, t('settings.import')))
      : null));
    return;
  }

  const typeCards = h('div.type-grid', state.types.map((ty) =>
    h('a.type-card', { href: `#/cave/list?type=${ty.code}`, style: { '--type-color': ty.color } },
      h('span.type-count', s.by_type[ty.code] || 0),
      h('span.type-name', typeDot(ty.code), typeLabel(ty.code)))));

  const totals = h('div.kpis',
    kpi(t('summary.total'), s.total),
    kpi(t('summary.references'), s.references),
    kpi(t('summary.value'), fmtPrice(s.value || null)),
    s.unplaced ? h('a.kpi.warn', { href: '#/cave/plan' }, h('strong', s.unplaced), h('span', t('summary.unplaced'))) : null);

  const totalPhase = PHASES.reduce((n, p) => n + (s.by_phase[p] || 0), 0) || 1;
  const phaseBar = h('div.phase-bar', { role: 'img', 'aria-label': t('summary.schedule') },
    PHASES.filter((p) => s.by_phase[p]).map((p) =>
      h(`span.seg.phase-${p}`, { style: { flexGrow: s.by_phase[p] }, title: `${t(`phase.${p}`)} : ${s.by_phase[p]}` })));
  const phaseList = h('ul.phase-list', PHASES.map((p) => h('li',
    h('a', { href: `#/cave/schedule?phase=${p}` }, h(`span.swatch.phase-${p}`), t(`phase.${p}`)),
    h('strong', s.by_phase[p] || 0),
    h('small.muted', `${Math.round(((s.by_phase[p] || 0) * 100) / totalPhase)} %`))));

  const priority = s.priority.length
    ? h('ul.list', s.priority.map((r) => h('li', h('a.list-row', { href: `#/wine/${r.wine_id}` },
      typeDot(r.type),
      h('span.grow', h('strong', r.name), h('small.muted', ` ${r.appellation || ''} · ${fmtYear(r.year)}`),
        h('small.block.muted', windowText(r))),
      phaseBadge(r.phase, r.overdue),
      h('span.stock', `× ${r.stock}`)))))
    : h('p.muted', t('summary.no_priority'));

  const storages = s.storages.length
    ? h('ul.list', s.storages.map((st) => h('li.list-row',
      h('span.grow', h('strong', st.name), h('small.muted', st.kind === 'rack' ? ` ${st.rows} × ${st.cols}` : ` ${t('storage.box')}`)),
      st.capacity ? h('meter', { min: 0, max: st.capacity, value: st.count, title: `${st.count}/${st.capacity}` }) : null,
      h('span.stock', st.capacity ? `${st.count}/${st.capacity}` : st.count))))
    : h('p.muted', t('summary.no_storage'), ' ', canWrite() ? h('a', { href: '#/settings/storages' }, t('summary.configure')) : null);

  clear(el,
    head,
    typeCards,
    totals,
    h('div.columns',
      h('section.card', h('h2', t('summary.schedule'), h('small.muted', ` ${s.year}`)), phaseBar, phaseList),
      h('section.card', h('h2', t('summary.priority')), priority)),
    h('section.card', h('h2', h('a', { href: '#/cave/plan' }, t('summary.storages'))), storages));
}

function kpi(label, value) {
  return h('div.kpi', h('strong', value), h('span', label));
}
