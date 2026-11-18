// Core helpers shared by every page: API client, i18n, DOM builder, formatting, dialogs, toasts.
// No framework, no build step: plain ES modules served as static files.

export const state = {
  session: null,   // {authenticated, user:{username, role, can_write}, setup_required, version, image_generation}
  types: [],       // [{code, label_fr, label_en, color}]
  lang: localStorage.getItem('cellarium.lang') || 'fr',
  dict: {},
  fallback: {},
};

// ------------------------------------------------------------------ i18n

export const LANGS = { fr: 'Français', en: 'English' };

export async function loadLang(lang) {
  if (!LANGS[lang]) lang = 'fr';
  const load = async (l) => (await fetch(`/static/i18n/${l}.json`, { cache: 'no-cache' })).json();
  state.dict = await load(lang);
  state.fallback = lang === 'fr' ? state.dict : await load('fr');
  state.lang = lang;
  localStorage.setItem('cellarium.lang', lang);
  document.documentElement.lang = lang;
}

/** t("some.key", {n: 3}) — "{n}" placeholders; missing keys fall back to French then to the key. */
export function t(key, params = {}) {
  let text = state.dict[key] ?? state.fallback[key] ?? key;
  for (const [k, v] of Object.entries(params)) text = text.replaceAll(`{${k}}`, v);
  return text;
}

export function typeInfo(code) {
  return state.types.find((x) => x.code === code) || { code, label_fr: code, label_en: code, color: '#999' };
}

export function typeLabel(code) {
  const info = typeInfo(code);
  return state.lang === 'fr' ? info.label_fr : info.label_en;
}

// ------------------------------------------------------------------ API

export class ApiError extends Error {
  constructor(status, detail, body) {
    super(typeof detail === 'string' ? detail : JSON.stringify(detail));
    this.status = status;
    this.body = body;
  }
}

export async function api(path, { method = 'GET', body, form, raw } = {}) {
  const opts = { method, headers: { 'X-Cellarium': '1' }, credentials: 'same-origin' };
  if (form) opts.body = form;
  else if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(`/api${path}`, opts);
  if (raw) return res;
  const data = res.headers.get('content-type')?.includes('json') ? await res.json() : await res.text();
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith('/login') && !path.startsWith('/session')) {
      location.hash = '#/login';
    }
    const detail = data?.detail ?? data;
    throw new ApiError(res.status, Array.isArray(detail) ? detail.map((d) => d.msg).join(', ') : detail, data);
  }
  return data;
}

export const canWrite = () => !!state.session?.user?.can_write;
export const isOwner = () => state.session?.user?.role === 'owner';

// ------------------------------------------------------------------ DOM

/** h('div.card#id', {onclick, class, ...attrs}, ...children) — children: Node | string | array | null */
export function h(tag, attrs, ...children) {
  if (attrs === null || typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs)) {
    children.unshift(attrs);
    attrs = {};
  }
  const [, name = 'div', rest = ''] = tag.match(/^([a-z0-9-]*)(.*)$/i);
  const el = document.createElement(name || 'div');
  for (const part of rest.match(/[.#][^.#]+/g) || []) {
    if (part[0] === '.') el.classList.add(part.slice(1));
    else el.id = part.slice(1);
  }
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className += (el.className ? ' ' : '') + v;
    else if (k === 'style' && typeof v === 'object') {
      for (const [prop, val] of Object.entries(v)) {
        if (prop.startsWith('--')) el.style.setProperty(prop, val);
        else el.style[prop] = val;
      }
    }
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k in el && typeof v !== 'string') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function clear(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

// Feather-style stroke icons (MIT), inlined to stay build-free.
const ICONS = {
  home: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/>',
  cellar: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="1.6"/><circle cx="16" cy="8" r="1.6"/><circle cx="8" cy="16" r="1.6"/><circle cx="16" cy="16" r="1.6"/>',
  add: '<path d="M12 5v14M5 12h14"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>',
  back: '<path d="m15 18-6-6 6-6"/>',
  move: '<path d="M17 3l4 4-4 4M3 7h18M7 21l-4-4 4-4M21 17H3"/>',
  ai: '<path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
  photo: '<path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>',
  close: '<path d="M18 6 6 18M6 6l12 12"/>',
  up: '<path d="m18 15-6-6-6 6"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  restore: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
};

export function icon(name) {
  const span = h('span.icon', { 'aria-hidden': 'true' });
  span.innerHTML = `<svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ''}</svg>`;
  return span;
}

// ------------------------------------------------------------------ formatting

export function fmtPrice(v) {
  if (v === null || v === undefined || v === '') return '—';
  return new Intl.NumberFormat(state.lang === 'fr' ? 'fr-FR' : 'en-GB', { style: 'currency', currency: 'EUR' }).format(v);
}

export function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso.length === 10 ? iso + 'T12:00:00' : iso.replace(' ', 'T') + 'Z');
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(state.lang === 'fr' ? 'fr-FR' : 'en-GB');
}

export function fmtVolume(cl) {
  if (!cl) return '—';
  return cl >= 100 ? `${(cl / 100).toLocaleString(state.lang)} L` : `${Number(cl).toLocaleString(state.lang)} cl`;
}

export const fmtYear = (y) => (y ? String(y) : t('wine.nv'));

export function roman(n) {
  const map = [[1000, 'M'], [900, 'CM'], [500, 'D'], [400, 'CD'], [100, 'C'], [90, 'XC'], [50, 'L'], [40, 'XL'],
    [10, 'X'], [9, 'IX'], [5, 'V'], [4, 'IV'], [1, 'I']];
  let out = '';
  for (const [v, s] of map) while (n >= v) { out += s; n -= v; }
  return out;
}

export function locationLabel(loc) {
  if (!loc) return t('plan.unplaced_short');
  if (loc.kind === 'rack' && loc.row) return t('plan.slot', { name: loc.storage_name, row: loc.row, col: roman(loc.col) });
  return loc.storage_name;
}

export function windowText(v) {
  if (!v) return '—';
  const peak = v.peak_from && v.peak_to && v.peak_from !== v.peak_to ? `${v.peak_from}–${v.peak_to}` : v.peak_from || v.peak_to;
  const parts = [];
  if (v.drink_from) parts.push(t('window.from', { y: v.drink_from }));
  if (peak) parts.push(t('window.peak', { y: peak }));
  if (v.drink_before) parts.push(t('window.before', { y: v.drink_before }));
  return parts.length ? parts.join(' · ') : '—';
}

export function typeDot(code) {
  return h('span.type-dot', { style: { background: typeInfo(code).color }, title: typeLabel(code) });
}

export function phaseBadge(phase, overdue) {
  return h(`span.badge.phase-${phase}`, overdue ? t('phase.overdue') : t(`phase.${phase}`));
}

/** Text with AI-written parts: lines starting with "[IA 2026-11-18]" get an "IA" badge. */
export function richText(text) {
  if (!text) return null;
  return String(text).split('\n').map((line, i) => {
    const m = line.match(/^\[IA(?: (\d{4}-\d{2}-\d{2}))?\]\s*/);
    const br = i ? h('br') : null;
    if (!m) return [br, line];
    const title = m[1] ? t('wine.ai_comment_on', { date: fmtDate(m[1]) }) : t('wine.ai_comment');
    return [br, h('span.ai-badge', { title, 'aria-label': title }, icon('ai'), ' IA'), ' ', line.slice(m[0].length)];
  });
}

export const normalize = (s) => (s || '').normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase();

// ------------------------------------------------------------------ feedback

export function toast(message, kind = 'info') {
  let box = document.getElementById('toasts');
  if (!box) {
    box = h('div#toasts', { role: 'status', 'aria-live': 'polite' });
    document.body.append(box);
  }
  const el = h(`div.toast.${kind}`, message);
  box.append(el);
  setTimeout(() => el.classList.add('hide'), 3500);
  setTimeout(() => el.remove(), 4000);
}

export function errorToast(err) {
  console.error(err);
  toast(err instanceof ApiError ? err.message : t('error.generic'), 'error');
}

/** Modal dialog. content: Node; actions: [{label, kind, onclick -> false keeps it open}] */
export function dialog(title, content, actions = []) {
  return new Promise((resolve) => {
    const dlg = h('dialog.modal');
    const close = (value) => { dlg.close(); dlg.remove(); resolve(value); };
    const footer = h('div.modal-actions',
      actions.map((a) => h(`button.btn${a.kind ? '.' + a.kind : ''}`, {
        type: 'button',
        onclick: async () => {
          const r = a.onclick ? await a.onclick() : a.value;
          if (r !== false) close(r === undefined ? a.value : r);
        },
      }, a.label)));
    dlg.append(
      h('header.modal-head', h('h2', title), h('button.icon-btn', { type: 'button', 'aria-label': t('action.close'), onclick: () => close(null) }, icon('close'))),
      h('div.modal-body', content),
      footer,
    );
    dlg.addEventListener('cancel', (e) => { e.preventDefault(); close(null); });
    document.body.append(dlg);
    dlg.showModal();
  });
}

export async function confirmDialog(message, label = t('action.confirm'), kind = 'danger') {
  return dialog(t('action.confirm_title'), h('p', message), [
    { label: t('action.cancel'), value: false },
    { label, kind, value: true },
  ]);
}

// ------------------------------------------------------------------ forms

export function field(label, input, hint) {
  return h('label.field', h('span.field-label', label), input, hint ? h('small.hint', hint) : null);
}

export function formData(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name || el.disabled) continue;
    if (el.type === 'checkbox') out[el.name] = el.checked;
    else out[el.name] = el.value.trim() === '' ? null : el.value.trim();
  }
  return out;
}

export function typeSelect(name, value) {
  return h('select', { name, required: true },
    state.types.map((ty) => h('option', { value: ty.code, selected: ty.code === value }, typeLabel(ty.code))));
}

export function loading() {
  return h('div.loading', t('common.loading'));
}

export function emptyState(text, action) {
  return h('div.empty', h('p', text), action || null);
}
