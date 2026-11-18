// "Ma cave" with four modes: sortable list, drinking schedule, graphical plan, history.
import {
  api, canWrite, clear, confirmDialog, dialog, emptyState, errorToast, fmtDate, fmtPrice, fmtVolume, fmtYear, h, icon,
  locationLabel, normalize, phaseBadge, roman, state, t, toast, typeDot, typeLabel, windowText,
} from '../core.js';
import { bottleDialog } from './bottles.js';

const MODES = ['list', 'schedule', 'plan', 'history'];
const SCHEDULE_ORDER = ['keep', 'drink', 'peak', 'decline', 'unknown'];

export async function renderCellar(mode, query, el) {
  const tabs = h('nav.tabs', { 'aria-label': t('cellar.modes') }, MODES.map((m) =>
    h('a.tab', { href: `#/cave/${m}`, class: m === mode ? 'active' : '', 'aria-current': m === mode ? 'page' : null },
      t(`cellar.mode.${m}`))));
  const body = h('div.mode-body');
  clear(el, h('div.page-head', h('h1', t('cellar.title')),
    canWrite() ? h('a.btn.primary', { href: '#/wine/new' }, icon('add'), ' ', t('nav.add')) : null), tabs, body);
  const render = { list: renderList, schedule: renderSchedule, plan: renderPlan, history: renderHistory }[mode];
  await render(body, query);
}

// Keep the URL in sync with filters without triggering a re-render.
function setQuery(params) {
  const [path] = location.hash.split('?');
  const q = new URLSearchParams(params);
  for (const [k, v] of [...q]) if (!v) q.delete(k);
  history.replaceState(null, '', `${path}${q.toString() ? '?' + q : ''}`);
}

// ------------------------------------------------------------------ list

const COLUMNS = [
  ['appellation', 'col.appellation'], ['name', 'col.name'], ['year', 'col.year'], ['type', 'col.type'],
  ['volume_cl', 'col.volume'], ['price', 'col.price'], ['stock', 'col.stock'],
];

async function renderList(body, query) {
  const filters = {
    q: query.get('q') || '', type: query.get('type') || '', sort: query.get('sort') || 'appellation',
    dir: query.get('dir') || 'asc', empty: query.get('empty') === '1',
  };
  let rows = await api(`/cellar${filters.empty ? '?include_empty=true' : ''}`);
  const tableHost = h('div');
  const counter = h('p.muted.counter');

  const search = h('input.search', {
    type: 'search', placeholder: t('cellar.search'), value: filters.q, 'aria-label': t('cellar.search'),
    oninput: () => { filters.q = search.value; sync(); draw(); },
  });
  const chips = h('div.chips', h('button.chip', { type: 'button', class: !filters.type ? 'active' : '', onclick: () => pick('') }, t('common.all')),
    state.types.map((ty) => h('button.chip', { type: 'button', class: filters.type === ty.code ? 'active' : '', dataset: { type: ty.code }, onclick: () => pick(ty.code) },
      typeDot(ty.code), typeLabel(ty.code))));
  const empty = h('label.check', h('input', {
    type: 'checkbox', checked: filters.empty, onchange: async (e) => {
      filters.empty = e.target.checked; sync();
      rows = await api(`/cellar${filters.empty ? '?include_empty=true' : ''}`); draw();
    },
  }), t('cellar.show_empty'));
  const sortSelect = h('select.mobile-only', {
    'aria-label': t('cellar.sort'),
    onchange: (e) => { [filters.sort, filters.dir] = e.target.value.split(':'); sync(); draw(); },
  }, COLUMNS.flatMap(([key, label]) => ['asc', 'desc'].map((dir) =>
    h('option', { value: `${key}:${dir}`, selected: filters.sort === key && filters.dir === dir }, `${t(label)} ${dir === 'asc' ? '↑' : '↓'}`))));

  function pick(type) {
    filters.type = type; sync();
    chips.querySelectorAll('.chip').forEach((c) => c.classList.toggle('active', (c.dataset.type || '') === type));
    draw();
  }
  function sync() {
    setQuery({ q: filters.q, type: filters.type, sort: filters.sort !== 'appellation' ? filters.sort : '',
      dir: filters.dir !== 'asc' ? filters.dir : '', empty: filters.empty ? '1' : '' });
  }
  function sorted() {
    const words = normalize(filters.q).split(/\s+/).filter(Boolean);
    const list = rows.filter((r) => (!filters.type || r.type === filters.type) &&
      words.every((w) => normalize(`${r.name} ${r.appellation} ${r.region} ${r.producer} ${r.year || ''}`).includes(w)));
    const key = filters.sort;
    const dir = filters.dir === 'desc' ? -1 : 1;
    const collator = new Intl.Collator(state.lang, { sensitivity: 'base', numeric: true });
    return list.sort((a, b) => {
      let va = a[key]; let vb = b[key];
      if (key === 'type') { va = typeLabel(va); vb = typeLabel(vb); }
      if (va === null || va === undefined) return 1;
      if (vb === null || vb === undefined) return -1;
      const c = typeof va === 'number' ? va - vb : collator.compare(va, vb);
      return c * dir || collator.compare(a.name, b.name);
    });
  }
  function draw() {
    const list = sorted();
    const bottles = list.reduce((n, r) => n + r.stock, 0);
    counter.textContent = t('cellar.counter', { refs: list.length, bottles });
    if (!list.length) { clear(tableHost, emptyState(rows.length ? t('cellar.no_match') : t('cellar.empty'))); return; }
    const head = h('tr', COLUMNS.map(([key, label]) => {
      const active = filters.sort === key;
      return h('th', { scope: 'col', 'aria-sort': active ? (filters.dir === 'asc' ? 'ascending' : 'descending') : null, class: `col-${key}` },
        h('button.sort', {
          type: 'button', onclick: () => {
            filters.dir = active && filters.dir === 'asc' ? 'desc' : 'asc'; filters.sort = key; sync(); draw();
            sortSelect.value = `${filters.sort}:${filters.dir}`;
          },
        }, t(label), active ? (filters.dir === 'asc' ? ' ▲' : ' ▼') : ''));
    }));
    const tbody = h('tbody', list.map((r) => h('tr.clickable', {
      tabindex: 0,
      onclick: () => { location.hash = `#/wine/${r.wine_id}`; },
      onkeydown: (e) => { if (e.key === 'Enter') location.hash = `#/wine/${r.wine_id}`; },
      class: r.stock ? '' : 'finished',
    },
    h('td.col-appellation', { 'data-label': t('col.appellation') }, r.appellation || '—'),
    h('td.col-name', { 'data-label': t('col.name') }, h('a', { href: `#/wine/${r.wine_id}` }, r.name)),
    h('td.col-year', { 'data-label': t('col.year') }, fmtYear(r.year)),
    h('td.col-type', { 'data-label': t('col.type') }, typeDot(r.type), ' ', typeLabel(r.type)),
    h('td.col-volume_cl', { 'data-label': t('col.volume') }, fmtVolume(r.volume_cl)),
    h('td.col-price.num', { 'data-label': t('col.price') }, fmtPrice(r.price)),
    h('td.col-stock.num', { 'data-label': t('col.stock') }, h('span.stock', r.stock)))));
    clear(tableHost, h('table.cellar-table', h('thead', head), tbody));
  }

  clear(body, h('div.toolbar', search, sortSelect, empty), chips, counter, tableHost);
  draw();
}

// ------------------------------------------------------------------ schedule

async function renderSchedule(body, query) {
  const data = await api('/schedule');
  const sections = SCHEDULE_ORDER.map((phase) => {
    const items = data.phases[phase];
    const count = items.reduce((n, r) => n + r.stock, 0);
    return h(`section.card.schedule-${phase}`, { id: `phase-${phase}` },
      h('h2', h(`span.swatch.phase-${phase}`), t(`phase.${phase}`), h('small.muted', ` · ${t('cellar.bottles', { n: count })}`)),
      h('p.muted.small', t(`phase.${phase}.help`)),
      items.length ? h('ul.list', items.map((r) => h('li', h('a.list-row', { href: `#/wine/${r.wine_id}` },
        typeDot(r.type),
        h('span.grow', h('strong', r.name), h('small.muted', ` ${r.appellation || ''} · ${fmtYear(r.year)}`),
          h('small.block.muted', windowText(r))),
        r.overdue ? phaseBadge(phase, true) : null,
        h('span.stock', `× ${r.stock}`))))) : h('p.muted', '—'));
  });
  clear(body, h('p.muted', t('schedule.year', { year: data.year })), sections);
  const phase = query.get('phase');
  if (phase) document.getElementById(`phase-${phase}`)?.scrollIntoView({ behavior: 'smooth' });
}

// ------------------------------------------------------------------ plan

async function renderPlan(body, query) {
  const data = await api('/plan');
  const all = [...data.storages.flatMap((s) => s.bottles), ...data.unplaced];
  const byId = new Map(all.map((b) => [b.id, b]));
  let moving = byId.get(Number(query.get('move'))) || null;
  const focus = Number(query.get('bottle')) || null;
  let filter = '';

  const banner = h('div.move-banner', { role: 'status' });
  const search = h('input.search', {
    type: 'search', placeholder: t('plan.search'), 'aria-label': t('plan.search'),
    oninput: () => { filter = normalize(search.value); applyFilter(); },
  });
  const legend = h('div.legend', state.types.map((ty) => h('span', typeDot(ty.code), typeLabel(ty.code))));
  const host = h('div.plan');

  function matches(b) {
    if (!filter) return true;
    return filter.split(/\s+/).every((w) => normalize(`${b.name} ${b.appellation} ${b.year || ''}`).includes(w));
  }
  function applyFilter() {
    host.querySelectorAll('[data-bottle]').forEach((node) => {
      const b = byId.get(Number(node.dataset.bottle));
      node.classList.toggle('match', !!filter && matches(b));
      node.classList.toggle('dim', !!filter && !matches(b));
    });
  }
  function drawBanner() {
    banner.hidden = !moving;
    if (!moving) return;
    clear(banner, h('span', t('plan.moving', { name: `${moving.name} ${fmtYear(moving.year)}`, from: locationLabel(moving.location) })),
      h('button.btn.small', { type: 'button', onclick: () => { moving = null; setQuery({}); draw(); } }, t('action.cancel')));
  }

  async function doMove(target) {
    const bottle = moving;
    if (!bottle) return;
    try {
      let res;
      try {
        res = await api(`/bottles/${bottle.id}/move`, { method: 'POST', body: target });
      } catch (err) {
        if (err.status !== 409 || !err.body?.bottle_id) throw err;
        if (!(await confirmDialog(t('plan.swap_confirm'), t('plan.swap'), 'primary'))) return;
        res = await api(`/bottles/${bottle.id}/move`, { method: 'POST', body: { ...target, swap: true } });
      }
      toast(t('plan.moved', { to: locationLabel(res.location) }), 'success');
      moving = null;
      setQuery({});
      await renderPlan(body, new URLSearchParams());
    } catch (err) { errorToast(err); }
  }

  async function onBottle(b) {
    if (moving) {
      if (moving.id === b.id) return;
      const loc = b.location;
      await doMove({ storage_id: loc?.storage_id ?? null, row: loc?.row ?? null, col: loc?.col ?? null });
      return;
    }
    const result = await bottleDialog(b);
    if (result === 'move') { moving = b; draw(); banner.scrollIntoView({ block: 'nearest' }); }
    if (result === 'out') await renderPlan(body, new URLSearchParams());
  }

  function bottleNode(b, tag = 'button.slot.full') {
    return h(tag, {
      type: 'button', dataset: { bottle: b.id }, title: `${b.appellation || ''} ${b.name} ${fmtYear(b.year)}`.trim(),
      style: { '--c': state.types.find((x) => x.code === b.type)?.color || '#999' },
      class: [moving?.id === b.id ? 'selected' : '', focus === b.id ? 'focus' : ''].join(' ').trim() || null,
      draggable: canWrite() ? 'true' : null,
      ondragstart: (e) => { e.dataTransfer.setData('text/plain', String(b.id)); e.dataTransfer.effectAllowed = 'move'; },
      onclick: () => onBottle(b),
      'aria-label': `${b.name} ${fmtYear(b.year)} — ${locationLabel(b.location)}`,
    });
  }

  function dropTarget(node, target) {
    if (!canWrite()) return node;
    node.addEventListener('dragover', (e) => { e.preventDefault(); node.classList.add('over'); });
    node.addEventListener('dragleave', () => node.classList.remove('over'));
    node.addEventListener('drop', async (e) => {
      e.preventDefault(); node.classList.remove('over');
      const b = byId.get(Number(e.dataTransfer.getData('text/plain')));
      if (b) { moving = b; await doMove(target); }
    });
    return node;
  }

  function rack(storage) {
    const occupied = new Map(storage.bottles.map((b) => [`${b.location.row}-${b.location.col}`, b]));
    const grid = h('div.rack-grid', { style: { '--cols': storage.cols } },
      h('span.corner'),
      Array.from({ length: storage.cols }, (_, c) => h('span.col-head', roman(c + 1))),
      Array.from({ length: storage.rows }, (_, r) => [
        h('span.row-head', r + 1),
        Array.from({ length: storage.cols }, (_, c) => {
          const b = occupied.get(`${r + 1}-${c + 1}`);
          const target = { storage_id: storage.id, row: r + 1, col: c + 1 };
          if (b) return dropTarget(bottleNode(b), target);
          return dropTarget(h('button.slot.empty', {
            type: 'button', 'aria-label': t('plan.empty_slot', { slot: `${r + 1}-${roman(c + 1)}` }),
            onclick: () => onEmptySlot(storage, r + 1, c + 1),
          }), target);
        }),
      ]));
    return h('section.card.storage', { id: `storage-${storage.id}` },
      h('h2', storage.name, h('small.muted', ` ${storage.count}/${storage.capacity}`)),
      h('div.rack-scroll', grid));
  }

  function chipList(bottles) {
    return h('div.bottle-chips', bottles.map((b) => {
      const chip = bottleNode(b, 'button.bottle-chip');
      chip.append(h('span.dot'), h('span', `${b.name} ${fmtYear(b.year)}`));
      return chip;
    }));
  }

  function box(storage) {
    const target = { storage_id: storage.id };
    return dropTarget(h('section.card.storage.box', { id: `storage-${storage.id}` },
      h('h2', storage.name, h('small.muted', ` ${t('storage.box')} · ${storage.count}`)),
      moving ? h('button.btn.small', { type: 'button', onclick: () => doMove(target) }, t('plan.drop_here')) : null,
      storage.bottles.length ? chipList(storage.bottles) : h('p.muted', t('plan.box_empty'))), target);
  }

  function draw() {
    drawBanner();
    const unplaced = dropTarget(h('section.card.storage.unplaced',
      h('h2', t('plan.unplaced'), h('small.muted', ` ${data.unplaced.length}`)),
      moving && moving.location ? h('button.btn.small', { type: 'button', onclick: () => doMove({ storage_id: null }) }, t('plan.drop_unplaced')) : null,
      data.unplaced.length ? h('div', h('p.muted.small', canWrite() ? t('plan.unplaced_help') : ''), chipList(data.unplaced))
        : h('p.muted', t('plan.all_placed'))), { storage_id: null });
    clear(host,
      data.storages.length ? null : emptyState(t('plan.no_storage'), canWrite() ? h('a.btn.primary', { href: '#/settings/storages' }, t('summary.configure')) : null),
      data.storages.map((s) => (s.kind === 'rack' ? rack(s) : box(s))),
      unplaced);
    applyFilter();
  }

  // Clicking an empty slot: finish a move, or pick an unplaced bottle to put there.
  async function onEmptySlot(storage, row, col) {
    if (moving) { await doMove({ storage_id: storage.id, row, col }); return; }
    if (!canWrite() || !data.unplaced.length) return;
    let chosen = null;
    const list = h('ul.list', data.unplaced.map((b) => h('li', h('label.list-row',
      h('input', { type: 'radio', name: 'pick', value: b.id, onchange: () => { chosen = b; } }),
      typeDot(b.type), h('span.grow', h('strong', b.name), ` ${fmtYear(b.year)}`, h('small.block.muted', b.appellation || ''))))));
    const ok = await dialog(t('plan.place_here', { slot: `${storage.name} ${row}-${roman(col)}` }), list, [
      { label: t('action.cancel'), value: false },
      { label: t('plan.place'), kind: 'primary', value: true },
    ]);
    if (ok && chosen) { moving = chosen; await doMove({ storage_id: storage.id, row, col }); }
  }

  clear(body, h('div.toolbar', search), legend, banner, host);
  draw();
  if (focus) {
    const node = host.querySelector(`[data-bottle="${focus}"]`);
    node?.scrollIntoView({ block: 'center', inline: 'center' });
  }
}

// ------------------------------------------------------------------ history

const KIND_ICON = { in: '＋', out: '－', move: '⇄', restore: '↺' };

async function renderHistory(body, query) {
  const filters = { kind: query.get('kind') || '', year: query.get('year') || '' };
  const host = h('div');
  const thisYear = new Date().getFullYear();
  const kindSel = h('select', { 'aria-label': t('history.kind'), onchange: (e) => { filters.kind = e.target.value; load(); } },
    h('option', { value: '' }, t('history.all')),
    ['in', 'out', 'move', 'restore'].map((k) => h('option', { value: k, selected: filters.kind === k }, t(`history.kind.${k}`))));
  const yearSel = h('select', { 'aria-label': t('history.year'), onchange: (e) => { filters.year = e.target.value; load(); } },
    h('option', { value: '' }, t('history.all_years')),
    Array.from({ length: 15 }, (_, i) => thisYear - i).map((y) => h('option', { value: y, selected: String(y) === filters.year }, y)));

  async function load() {
    setQuery(filters);
    const params = new URLSearchParams(Object.entries(filters).filter(([, v]) => v));
    const items = await api(`/history?${params}`);
    if (!items.length) { clear(host, emptyState(t('history.empty'))); return; }
    const groups = new Map();
    for (const m of items) {
      if (!groups.has(m.date)) groups.set(m.date, []);
      groups.get(m.date).push(m);
    }
    clear(host, [...groups].map(([date, list]) => h('section.history-day',
      h('h3', fmtDate(date)),
      h('ul.list', list.map((m) => h('li.history-item',
        h(`span.kind.kind-${m.kind}`, { title: t(`history.kind.${m.kind}`) }, KIND_ICON[m.kind] || '•'),
        h('div.grow',
          h('div', m.type ? typeDot(m.type) : null, ' ',
            m.wine_id ? h('a', { href: `#/wine/${m.wine_id}` }, m.label) : h('span', m.label),
            m.count > 1 ? h('strong', ` × ${m.count}`) : null),
          h('small.muted.block', [
            m.kind === 'out' ? t(`out.${m.reason}`) : m.kind === 'in' ? t(`in.${m.reason || 'purchase'}`) : t(`history.kind.${m.kind}`),
            m.kind === 'move' || m.from_loc ? ` · ${t('history.from', { loc: m.from_loc || t('plan.unplaced_short') })}` : '',
            m.kind === 'move' || m.to_loc ? ` · ${t('history.to', { loc: m.to_loc || t('plan.unplaced_short') })}` : '',
            m.actor ? ` · ${m.actor}` : '',
          ].join('')),
          m.note ? h('small.block.note', m.note) : null),
        canWrite() && m.kind === 'out' && m.bottle_id ? h('button.btn.small', {
          type: 'button', title: t('history.restore'),
          onclick: async () => {
            if (!(await confirmDialog(t('history.restore_confirm'), t('history.restore'), 'primary'))) return;
            try { await api(`/bottles/${m.bottle_id}/restore`, { method: 'POST' }); toast(t('history.restored'), 'success'); load(); } catch (err) { errorToast(err); }
          },
        }, icon('restore'), ' ', t('history.restore')) : null))))));
  }

  clear(body, h('div.toolbar', kindSel, yearSel), host);
  await load();
}
