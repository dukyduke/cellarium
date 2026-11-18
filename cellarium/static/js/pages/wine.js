// Wine sheet ("fiche vin") and new-wine form.
import {
  api, canWrite, clear, confirmDialog, dialog, errorToast, field, fmtDate, fmtPrice, fmtVolume, fmtYear, formData, h,
  icon, locationLabel, normalize, phaseBadge, state, t, toast, typeDot, typeInfo, typeLabel, typeSelect, windowText,
} from '../core.js';
import { outDialog } from './bottles.js';

const VOLUMES = [18.7, 37.5, 50, 62, 70, 75, 100, 150, 300, 600];
const TEXT_FIELDS = ['appellation', 'producer', 'region', 'country', 'grapes', 'serving_temp'];

// ------------------------------------------------------------------ helpers

function aiMark(meta, key) {
  const m = meta?.[key];
  if (!m || m.origin !== 'ai') return null;
  const title = `${t('wine.ai_filled')}${m.sources?.length ? '\n' + m.sources.join('\n') : ''}`;
  return h('span.ai-badge', { title, 'aria-label': title }, icon('ai'), ' IA');
}

function bottleSvg(type) {
  const color = typeInfo(type).color;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 320"><rect x="48" y="10" width="24" height="70" rx="6" fill="${color}" opacity=".85"/><path d="M48 80c0 30-28 40-28 80v130a20 20 0 0 0 20 20h40a20 20 0 0 0 20-20V160c0-40-28-50-28-80z" fill="${color}"/><rect x="30" y="170" width="60" height="70" rx="6" fill="#faf7f4" opacity=".9"/></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

function volumeSelect(name, value = 75) {
  return h('select', { name }, VOLUMES.map((v) => h('option', { value: v, selected: v === Number(value) }, fmtVolume(v))));
}

async function storageOptions() {
  const storages = await api('/storages');
  return h('select', { name: 'storage_id' },
    h('option', { value: '' }, t('plan.unplaced_short')),
    storages.map((s) => h('option', { value: s.id },
      s.kind === 'rack' ? `${s.name} (${t('storage.free', { n: s.capacity - s.count })})` : `${s.name} (${t('storage.box')})`)));
}

function wineFields(w = {}) {
  return [
    field(`${t('col.name')} *`, h('input', { name: 'name', required: true, value: w.name || '', autocomplete: 'off' }), t('wine.name_hint')),
    field(`${t('col.type')} *`, typeSelect('type', w.type || 'red')),
    h('div.grid-2', TEXT_FIELDS.map((k) => field(t(`wine.${k}`), h('input', { name: k, value: w[k] || '', list: `dl-${k}` })))),
    field(t('wine.description'), h('textarea', { name: 'description', rows: 4 }, w.description || '')),
    field(t('wine.food_pairing'), h('textarea', { name: 'food_pairing', rows: 2 }, w.food_pairing || '')),
    field(t('wine.notes'), h('textarea', { name: 'notes', rows: 3 }, w.notes || ''), t('wine.notes_hint')),
  ];
}

async function datalists() {
  const wines = await api('/wines?limit=1000');
  return ['appellation', 'region', 'country', 'producer'].map((k) => h('datalist', { id: `dl-${k}` },
    [...new Set(wines.map((w) => w[k]).filter(Boolean))].sort().map((v) => h('option', { value: v }))));
}

function bottleFields(storageSelect, defaults = {}) {
  const today = new Date().toISOString().slice(0, 10);
  return h('div.grid-2',
    field(t('col.year'), h('input', { name: 'year', type: 'number', min: 1800, max: 2200, placeholder: t('wine.nv'), value: defaults.year ?? '' })),
    field(t('bottle.quantity'), h('input', { name: 'quantity', type: 'number', min: defaults.allowZero ? 0 : 1, max: 500, value: defaults.quantity ?? 1, required: true })),
    field(t('col.volume'), volumeSelect('volume_cl', defaults.volume_cl)),
    field(t('bottle.unit_price'), h('input', { name: 'unit_price', type: 'number', min: 0, step: '0.01', inputmode: 'decimal' })),
    field(t('bottle.purchase_date'), h('input', { name: 'purchase_date', type: 'date', value: today })),
    field(t('bottle.supplier'), h('input', { name: 'supplier' })),
    field(t('bottle.storage'), storageSelect, t('bottle.storage_hint')),
    field(t('bottle.comment'), h('input', { name: 'comment' })));
}

function bottlePayload(data) {
  const out = { ...data };
  for (const k of ['quantity', 'storage_id']) out[k] = out[k] === null ? null : Number(out[k]);
  for (const k of ['volume_cl', 'unit_price']) out[k] = out[k] === null ? null : Number(String(out[k]).replace(',', '.'));
  out.year = out.year ? Number(out.year) : null;
  return out;
}

// ------------------------------------------------------------------ sheet

export async function renderWine(id, query, el) {
  const w = await api(`/wines/${id}`);
  const meta = w.meta || {};
  const write = canWrite();
  const vintages = w.vintages;
  let current = vintages.find((v) => v.stock > 0) || vintages[0] || null;

  // image
  const fileInput = h('input', {
    type: 'file', accept: 'image/*', capture: 'environment', hidden: true,
    onchange: async () => {
      const file = fileInput.files[0];
      if (!file) return;
      const form = new FormData();
      form.append('file', file);
      try { await api(`/wines/${id}/image`, { method: 'POST', form }); toast(t('wine.image_saved'), 'success'); renderWine(id, query, el); } catch (err) { errorToast(err); }
    },
  });
  const figure = h('figure.wine-image',
    h('img', { src: w.image ? `/api/media/${w.image}` : bottleSvg(w.type), alt: w.image ? w.name : '', loading: 'lazy' }),
    w.image_origin === 'ai' ? h('figcaption.muted', t('wine.image_ai')) : null,
    write ? h('div.row-actions',
      fileInput,
      h('button.btn.small', { type: 'button', onclick: () => fileInput.click() }, icon('photo'), ' ', t('wine.image_upload')),
      state.session.image_generation ? h('button.btn.small', {
        type: 'button', onclick: async (e) => {
          e.target.disabled = true; e.target.textContent = t('wine.image_generating');
          try { await api(`/wines/${id}/image/generate`, { method: 'POST' }); renderWine(id, query, el); } catch (err) { errorToast(err); e.target.disabled = false; }
        },
      }, icon('ai'), ' ', t('wine.image_generate')) : null,
      w.image ? h('button.btn.small.ghost', {
        type: 'button', onclick: async () => {
          if (!(await confirmDialog(t('wine.image_remove_confirm')))) return;
          await api(`/wines/${id}/image`, { method: 'DELETE' }); renderWine(id, query, el);
        },
      }, t('wine.image_remove')) : null) : null);

  // facts
  const windowCell = h('dd', current ? windowText(current) : '—');
  const facts = h('dl.facts',
    ['appellation', 'region', 'country', 'producer'].map((k) => [h('dt', t(`wine.${k}`)), h('dd', w[k] || '—', aiMark(meta, k))]),
    h('dt', t('col.type')), h('dd', typeDot(w.type), ' ', typeLabel(w.type)),
    h('dt', t('wine.grapes')), h('dd', w.grapes || '—', aiMark(meta, 'grapes')),
    h('dt', t('wine.serving_temp')), h('dd', w.serving_temp || '—', aiMark(meta, 'serving_temp')),
    h('dt', t('wine.window')), windowCell);

  const texts = ['description', 'food_pairing', 'notes'].filter((k) => w[k]).map((k) =>
    h('section.text-block', h('h3', t(`wine.${k}`), aiMark(meta, k)), h('p.pre', w[k])));
  const aiSources = [...new Set(Object.values(meta).filter((m) => m.origin === 'ai').flatMap((m) => m.sources || []))];
  for (const v of vintages) for (const m of Object.values(v.meta || {})) if (m.origin === 'ai') aiSources.push(...(m.sources || []));
  const sources = [...new Set(aiSources)];

  // reserve: vintage selector + window
  const windowBox = h('div');
  const vintageSelect = vintages.length > 1 ? h('select', {
    'aria-label': t('wine.vintage_select'),
    onchange: (e) => { current = vintages.find((v) => String(v.id) === e.target.value); drawWindow(); },
  }, vintages.map((v) => h('option', { value: v.id, selected: v === current }, `${fmtYear(v.year)} (${t('cellar.bottles', { n: v.stock })})`))) : null;

  function drawWindow() {
    windowCell.textContent = current ? windowText(current) : '—';
    if (!current) { clear(windowBox, h('p.muted', t('wine.no_vintage'))); return; }
    const v = current;
    const vm = v.meta || {};
    const cells = [['drink_from', 'window.drink_from'], ['peak_from', 'window.peak_from'], ['peak_to', 'window.peak_to'], ['drink_before', 'window.drink_before']];
    clear(windowBox,
      h('div.window-head', h('strong', t('wine.window_for', { year: fmtYear(v.year) })), ' ', phaseBadge(v.phase, v.drink_before && new Date().getFullYear() > v.drink_before)),
      h('dl.window', cells.map(([k, label]) => [h('dt', t(label)), h('dd', v[k] || '—', aiMark(vm, k))])),
      write ? h('button.btn.small', { type: 'button', onclick: () => editWindow(v) }, icon('edit'), ' ', t('wine.window_edit')) : null);
  }

  async function editWindow(v) {
    const form = h('form.grid-2', ['drink_from', 'peak_from', 'peak_to', 'drink_before'].map((k) =>
      field(t(`window.${k}`), h('input', { name: k, type: 'number', min: 1800, max: 2300, value: v[k] ?? '' }))));
    const ok = await dialog(t('wine.window_for', { year: fmtYear(v.year) }), h('div', h('p.muted.small', t('wine.window_help')), form), [
      { label: t('action.cancel'), value: false },
      {
        label: t('action.save'), kind: 'primary', onclick: async () => {
          const data = Object.fromEntries(Object.entries(formData(form)).map(([k, x]) => [k, x === null ? null : Number(x)]));
          try { await api(`/vintages/${v.id}`, { method: 'PATCH', body: data }); return true; } catch (err) { errorToast(err); return false; }
        },
      },
    ]);
    if (ok) renderWine(id, query, el);
  }

  // purchases (lots)
  const lots = w.lots.length ? h('div.table-wrap', h('table.data',
    h('thead', h('tr', ['bottle.purchase_date', 'col.year', 'col.volume', 'bottle.quantity', 'bottle.unit_price', 'bottle.comment'].map((k) => h('th', t(k))), write ? h('th', '') : null)),
    h('tbody', w.lots.map((l) => h('tr',
      h('td', { 'data-label': t('bottle.purchase_date') }, fmtDate(l.purchase_date)),
      h('td', { 'data-label': t('col.year') }, fmtYear(l.year)),
      h('td', { 'data-label': t('col.volume') }, fmtVolume(l.volume_cl)),
      h('td', { 'data-label': t('bottle.quantity') }, `${l.in_cellar} / ${l.quantity}`),
      h('td.num', { 'data-label': t('bottle.unit_price') }, fmtPrice(l.unit_price)),
      h('td', { 'data-label': t('bottle.comment') }, l.comment || (l.supplier ? `${t('bottle.supplier')} : ${l.supplier}` : '')),
      write ? h('td.actions',
        h('button.icon-btn', { type: 'button', title: t('action.edit'), 'aria-label': t('action.edit'), onclick: () => editLot(l) }, icon('edit')),
        h('button.icon-btn', {
          type: 'button', title: t('action.delete'), 'aria-label': t('action.delete'), onclick: async () => {
            if (!(await confirmDialog(t('wine.lot_delete_confirm', { n: l.quantity })))) return;
            try { await api(`/lots/${l.id}`, { method: 'DELETE' }); renderWine(id, query, el); } catch (err) { errorToast(err); }
          },
        }, icon('trash'))) : null))))) : h('p.muted', t('wine.no_purchase'));

  async function editLot(l) {
    const form = h('form.grid-2',
      field(t('bottle.purchase_date'), h('input', { name: 'purchase_date', type: 'date', value: l.purchase_date || '' })),
      field(t('col.year'), h('input', { name: 'year', type: 'number', placeholder: t('wine.nv'), value: l.year ?? '' })),
      field(t('col.volume'), volumeSelect('volume_cl', l.volume_cl)),
      field(t('bottle.unit_price'), h('input', { name: 'unit_price', type: 'number', step: '0.01', min: 0, value: l.unit_price ?? '' })),
      field(t('bottle.supplier'), h('input', { name: 'supplier', value: l.supplier || '' })),
      field(t('bottle.comment'), h('input', { name: 'comment', value: l.comment || '' })));
    const ok = await dialog(t('wine.lot_edit'), form, [
      { label: t('action.cancel'), value: false },
      {
        label: t('action.save'), kind: 'primary', onclick: async () => {
          const data = formData(form);
          data.year = data.year ? Number(data.year) : null;
          try { await api(`/lots/${l.id}`, { method: 'PATCH', body: data }); return true; } catch (err) { errorToast(err); return false; }
        },
      },
    ]);
    if (ok) renderWine(id, query, el);
  }

  // bottles in cellar
  const inCellar = w.bottles.filter((b) => b.status === 'in_cellar');
  const bottles = inCellar.length ? h('ul.list', inCellar.map((b) => h('li.list-row',
    typeDot(w.type),
    h('span.grow', h('strong', fmtYear(b.year)), h('small.muted', ` · ${fmtVolume(b.volume_cl)}`),
      h('small.block', h('a', { href: `#/cave/plan?bottle=${b.id}` }, locationLabel(b.location)))),
    write ? h('div.row-actions',
      h('a.btn.small', { href: `#/cave/plan?move=${b.id}`, title: t('bottle.move') }, icon('move'), h('span.hide-sm', ` ${t('bottle.move')}`)),
      h('button.btn.small.primary', {
        type: 'button', onclick: async () => { if (await outDialog({ ...b, name: w.name })) renderWine(id, query, el); },
      }, t('bottle.take_out')),
      h('button.icon-btn', {
        type: 'button', title: t('bottle.delete'), 'aria-label': t('bottle.delete'), onclick: async () => {
          if (!(await confirmDialog(t('bottle.delete_confirm')))) return;
          try { await api(`/bottles/${b.id}`, { method: 'DELETE' }); renderWine(id, query, el); } catch (err) { errorToast(err); }
        },
      }, icon('trash'))) : null))) : h('p.muted', t('wine.no_bottle'));

  const out = w.bottles.filter((b) => b.status !== 'in_cellar');
  const tasted = out.length ? h('ul.list', out.map((b) => h('li.list-row',
    h('span.grow', h('strong', fmtYear(b.year)), ` · ${t(`out.${b.status}`)} · ${fmtDate(b.out_date)}`,
      b.out_note ? h('small.block.note', b.out_note) : null)))) : null;

  async function addBottles() {
    const form = h('form', bottleFields(await storageOptions(), { year: current?.year ?? '' }));
    const ok = await dialog(t('wine.add_bottles'), form, [
      { label: t('action.cancel'), value: false },
      {
        label: t('action.add'), kind: 'primary', onclick: async () => {
          if (!form.reportValidity()) return false;
          try {
            const res = await api(`/wines/${id}/bottles`, { method: 'POST', body: bottlePayload(formData(form)) });
            toast(res.unplaced ? t('wine.added_unplaced', { n: res.bottle_ids.length, u: res.unplaced }) : t('wine.added', { n: res.bottle_ids.length }), 'success');
            return true;
          } catch (err) { errorToast(err); return false; }
        },
      },
    ]);
    if (ok) renderWine(id, query, el);
  }

  async function editWine() {
    const form = h('form.stack', await datalists(), wineFields(w));
    const ok = await dialog(t('wine.edit'), form, [
      { label: t('action.cancel'), value: false },
      {
        label: t('action.save'), kind: 'primary', onclick: async () => {
          if (!form.reportValidity()) return false;
          try { await api(`/wines/${id}`, { method: 'PATCH', body: formData(form) }); return true; } catch (err) { errorToast(err); return false; }
        },
      },
    ]);
    if (ok) renderWine(id, query, el);
  }

  async function deleteWine() {
    if (!(await confirmDialog(t('wine.delete_confirm', { name: w.name, n: w.stock })))) return;
    await api(`/wines/${id}`, { method: 'DELETE' });
    toast(t('wine.deleted'), 'success');
    location.hash = '#/cave';
  }

  clear(el,
    h('a.back', { href: '#/cave' }, icon('back'), ' ', t('cellar.title')),
    h('div.page-head.wine-head',
      h('div', h('h1', w.name), h('p.subtitle', [w.appellation, w.region].filter(Boolean).join(' · '))),
      write ? h('div.row-actions',
        h('button.btn', { type: 'button', onclick: editWine }, icon('edit'), ' ', t('action.edit')),
        h('button.btn.ghost.danger', { type: 'button', onclick: deleteWine }, icon('trash'), h('span.hide-sm', ` ${t('action.delete')}`))) : null),
    h('div.wine-top', figure, h('div', facts, texts,
      sources.length ? h('details.sources', h('summary', icon('ai'), ' ', t('wine.ai_sources', { n: sources.length })),
        h('ul', sources.map((s) => h('li', /^https?:\/\//.test(s) ? h('a', { href: s, target: '_blank', rel: 'noopener noreferrer' }, s) : s)))) : null)),
    h('section.card',
      h('div.card-head', h('h2', t('wine.reserve'), h('small.muted', ` · ${t('cellar.bottles', { n: w.stock })}`)),
        write ? h('button.btn.primary.small', { type: 'button', onclick: addBottles }, icon('add'), ' ', t('wine.add_bottles')) : null),
      vintageSelect ? field(t('wine.vintage_select'), vintageSelect) : null,
      windowBox,
      h('h3', t('wine.in_cellar')), bottles,
      h('h3', t('wine.purchases')), lots,
      tasted ? [h('h3', t('wine.tasted')), tasted] : null));
  drawWindow();
  if (query.get('add') === '1' && write) addBottles();
}

// ------------------------------------------------------------------ new wine

export async function renderNewWine(_query, el) {
  if (!canWrite()) { location.hash = '#/'; return; }
  const existing = await api('/wines?limit=1000');
  const suggestions = h('ul.suggestions');
  const storageSelect = await storageOptions();
  const form = h('form.stack', {
    onsubmit: async (e) => {
      e.preventDefault();
      const data = formData(form);
      const wine = {};
      for (const k of ['name', 'type', 'description', 'food_pairing', 'notes', ...TEXT_FIELDS]) wine[k] = data[k];
      const bottles = bottlePayload({
        year: data.year, quantity: data.quantity, volume_cl: data.volume_cl, unit_price: data.unit_price,
        purchase_date: data.purchase_date, supplier: data.supplier, comment: data.comment, storage_id: data.storage_id,
      });
      try {
        const created = await api('/wines', { method: 'POST', body: wine });
        if (bottles.quantity > 0) await api(`/wines/${created.id}/bottles`, { method: 'POST', body: bottles });
        toast(t('wine.created'), 'success');
        location.hash = `#/wine/${created.id}`;
      } catch (err) { errorToast(err); }
    },
  },
  await datalists(),
  h('section.card', h('h2', t('wine.identity')), wineFields()),
  suggestions,
  h('section.card', h('h2', t('wine.first_bottles')), h('p.muted.small', t('wine.first_bottles_hint')),
    bottleFields(storageSelect, { allowZero: true })),
  h('div.row-actions.sticky-actions', h('a.btn', { href: '#/cave' }, t('action.cancel')), h('button.btn.primary', { type: 'submit' }, t('wine.create'))));

  const nameInput = form.querySelector('[name=name]');
  nameInput.addEventListener('input', () => {
    const q = normalize(nameInput.value);
    const hits = q.length < 3 ? [] : existing.filter((x) => normalize(`${x.name} ${x.appellation}`).includes(q)).slice(0, 5);
    clear(suggestions, hits.length ? h('li.muted.small', t('wine.already_exists')) : null,
      hits.map((x) => h('li', h('a', { href: `#/wine/${x.id}?add=1` }, typeDot(x.type), ` ${x.name}`, x.appellation ? ` — ${x.appellation}` : '',
        h('small.muted', ` (${t('cellar.bottles', { n: x.stock })})`)))));
  });
  clear(el, h('a.back', { href: '#/cave' }, icon('back'), ' ', t('cellar.title')), h('h1', t('wine.new')), form);
  nameInput.focus();
}
