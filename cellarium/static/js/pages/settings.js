// Settings: storages (racks & boxes), import/export, accounts, API tokens, own account, wine types.
import {
  api, canWrite, clear, confirmDialog, dialog, emptyState, errorToast, field, fmtDate, formData, h, icon, isOwner,
  LANGS, loadLang, roman, state, t, toast, typeDot, typeLabel,
} from '../core.js';
import { refreshSession, route } from '../main.js';

const SECTIONS = [
  ['storages', () => true], ['import', () => true], ['users', isOwner], ['tokens', () => true],
  ['account', () => true], ['types', () => true], ['about', () => true],
];

export async function renderSettings(section, el) {
  const visible = SECTIONS.filter(([, show]) => show()).map(([k]) => k);
  if (!visible.includes(section)) section = 'storages';
  const body = h('div.settings-body');
  clear(el, h('h1', t('settings.title')),
    h('nav.tabs.scroll', visible.map((k) => h('a.tab', { href: `#/settings/${k}`, class: k === section ? 'active' : '' }, t(`settings.${k}`)))),
    body);
  await ({ storages, import: importExport, users, tokens, account, types, about })[section](body);
}

// ------------------------------------------------------------------ storages

async function storages(body) {
  const list = await api('/storages');
  const write = canWrite();
  const reload = () => storages(body);

  function preview(rows, cols) {
    const r = Math.min(rows || 0, 12); const c = Math.min(cols || 0, 16);
    return h('div.rack-preview', { style: { '--cols': c } }, Array.from({ length: r * c }, () => h('span')));
  }

  function storageForm(s = { kind: 'rack', rows: 4, cols: 6 }) {
    const pv = h('div');
    const kind = h('select', { name: 'kind' },
      h('option', { value: 'rack', selected: s.kind === 'rack' }, t('storage.rack')),
      h('option', { value: 'box', selected: s.kind === 'box' }, t('storage.box')));
    const rows = h('input', { name: 'rows', type: 'number', min: 1, max: 100, value: s.rows ?? 4 });
    const cols = h('input', { name: 'cols', type: 'number', min: 1, max: 100, value: s.cols ?? 6 });
    const dims = h('div.grid-2', field(t('storage.rows'), rows), field(t('storage.cols'), cols));
    const update = () => {
      dims.hidden = kind.value !== 'rack';
      rows.disabled = cols.disabled = kind.value !== 'rack';
      clear(pv, kind.value === 'rack' ? preview(Number(rows.value), Number(cols.value)) : h('p.muted.small', t('storage.box_help')));
    };
    [kind, rows, cols].forEach((x) => x.addEventListener('input', update));
    const form = h('form.stack',
      field(t('storage.name'), h('input', { name: 'name', required: true, value: s.name || '', placeholder: t('storage.name_placeholder') })),
      field(t('storage.kind'), kind), dims, pv,
      field(t('storage.notes'), h('input', { name: 'notes', value: s.notes || '' })));
    update();
    return form;
  }

  async function edit(s) {
    const form = storageForm(s || undefined);
    const ok = await dialog(s?.id ? t('storage.edit') : t('storage.add'), form, [
      { label: t('action.cancel'), value: false },
      {
        label: t('action.save'), kind: 'primary', onclick: async () => {
          if (!form.reportValidity()) return false;
          const data = formData(form);
          if (data.rows) data.rows = Number(data.rows);
          if (data.cols) data.cols = Number(data.cols);
          try {
            if (!s?.id) { await api('/storages', { method: 'POST', body: data }); return true; }
            try {
              await api(`/storages/${s.id}`, { method: 'PATCH', body: data });
            } catch (err) {
              if (err.status !== 409) throw err;
              const n = err.body?.bottles?.length || 0;
              if (!(await confirmDialog(t('storage.overflow_confirm', { n }), t('storage.overflow_ok'), 'primary'))) return false;
              await api(`/storages/${s.id}?unplace_overflow=true`, { method: 'PATCH', body: data });
            }
            return true;
          } catch (err) { errorToast(err); return false; }
        },
      },
    ]);
    if (ok) { toast(t('common.saved'), 'success'); reload(); }
  }

  async function remove(s) {
    if (!(await confirmDialog(t('storage.delete_confirm', { name: s.name, n: s.count })))) return;
    try { await api(`/storages/${s.id}`, { method: 'DELETE' }); reload(); } catch (err) { errorToast(err); }
  }

  async function reorder(s, delta) {
    const idx = list.indexOf(s);
    const other = list[idx + delta];
    if (!other) return;
    await api(`/storages/${s.id}`, { method: 'PATCH', body: { sort: other.sort } });
    await api(`/storages/${other.id}`, { method: 'PATCH', body: { sort: s.sort === other.sort ? s.sort + delta : s.sort } });
    reload();
  }

  clear(body,
    h('div.card-head', h('p.muted', t('storage.intro')), write ? h('button.btn.primary', { type: 'button', onclick: () => edit(null) }, icon('add'), ' ', t('storage.add')) : null),
    list.length ? h('ul.list.cards', list.map((s, i) => h('li.list-row',
      h('span.grow', h('strong', s.name),
        h('small.block.muted', s.kind === 'rack'
          ? t('storage.rack_desc', { rows: s.rows, cols: s.cols, cap: s.capacity, n: s.count, last: roman(s.cols) })
          : t('storage.box_desc', { n: s.count }))),
      write ? h('div.row-actions',
        h('button.icon-btn', { type: 'button', disabled: i === 0, 'aria-label': t('action.up'), onclick: () => reorder(s, -1) }, icon('up')),
        h('button.icon-btn', { type: 'button', disabled: i === list.length - 1, 'aria-label': t('action.down'), onclick: () => reorder(s, 1) }, icon('down')),
        h('button.icon-btn', { type: 'button', 'aria-label': t('action.edit'), title: t('action.edit'), onclick: () => edit(s) }, icon('edit')),
        h('button.icon-btn', { type: 'button', 'aria-label': t('action.delete'), title: t('action.delete'), onclick: () => remove(s) }, icon('trash'))) : null))) : emptyState(t('storage.none')));
}

// ------------------------------------------------------------------ import / export

async function importExport(body) {
  const write = canWrite();
  const result = h('div');
  const fileInput = h('input', { type: 'file', name: 'file', accept: '.csv,.xlsx,text/csv,.txt', required: true });
  const form = h('form.stack', {
    onsubmit: async (e) => {
      e.preventDefault();
      const fd = new FormData();
      fd.append('file', fileInput.files[0]);
      try { showPreview(await api('/import/preview', { method: 'POST', form: fd })); } catch (err) { errorToast(err); }
    },
  }, field(t('import.file'), fileInput, t('import.file_hint')), h('button.btn.primary', { type: 'submit' }, t('import.preview')));

  async function showPreview(p) {
    const ok = p.rows.filter((r) => !r.errors.length);
    const bottles = ok.reduce((n, r) => n + r.quantity, 0);
    const storageSel = h('select', { name: 'storage_id' }, h('option', { value: '' }, t('plan.unplaced_short')),
      (await api('/storages')).map((s) => h('option', { value: s.id }, s.name)));
    const mode = h('select', { name: 'mode' }, ['skip_existing', 'add', 'replace'].map((m) => h('option', { value: m }, t(`import.mode.${m}`))));
    clear(result, h('section.card',
      h('h2', t('import.preview_title')),
      h('p', t('import.summary', { format: p.format === 'vitiplace' ? 'Vitiplace' : 'CSV', rows: p.rows.length, ok: ok.length, bottles })),
      p.missing_columns?.length ? h('p.form-error', t('import.missing', { cols: p.missing_columns.join(', ') })) : null,
      p.unknown_columns?.length ? h('p.muted.small', t('import.ignored', { cols: p.unknown_columns.join(', ') })) : null,
      h('div.table-wrap', h('table.data.compact',
        h('thead', h('tr', ['import.line', 'col.appellation', 'col.name', 'col.year', 'col.type', 'col.stock', 'import.status'].map((k) => h('th', t(k))))),
        h('tbody', p.rows.map((r) => h('tr', { class: r.errors.length ? 'row-error' : '' },
          h('td', r.line), h('td', r.appellation || ''), h('td', r.name), h('td', r.year || t('wine.nv')),
          h('td', r.type ? [typeDot(r.type), ' ', typeLabel(r.type)] : '?'), h('td.num', r.quantity),
          h('td.small', [...r.errors, ...r.warnings].join(' ; ') || '✓')))))),
      h('div.grid-2', field(t('import.mode'), mode, t('import.mode_hint')), field(t('import.storage'), storageSel)),
      h('button.btn.primary', {
        type: 'button', disabled: !ok.length, onclick: async () => {
          if (mode.value === 'replace' && !(await confirmDialog(t('import.replace_confirm')))) return;
          try {
            const rep = await api('/import/commit', { method: 'POST', body: { rows: p.rows, mode: mode.value, storage_id: storageSel.value ? Number(storageSel.value) : null } });
            clear(result, h('section.card', h('h2', t('import.done')),
              h('p', t('import.report', { wines: rep.wines_created, bottles: rep.bottles_added, skipped: rep.skipped.length, errors: rep.errors.length })),
              h('a.btn.primary', { href: '#/cave/plan' }, t('import.go_plan'))));
          } catch (err) { errorToast(err); }
        },
      }, t('import.commit'))));
  }

  const restoreFile = h('input', { type: 'file', accept: '.json,application/json' });
  clear(body,
    write ? h('section.card', h('h2', t('import.title')), h('p.muted', t('import.intro')), form, result) : null,
    h('section.card', h('h2', t('export.title')), h('p.muted', t('export.intro')),
      h('div.row-actions',
        h('a.btn', { href: '/api/export.csv', download: 'cellarium.csv' }, t('export.csv')),
        h('a.btn', { href: '/api/export.json', download: 'cellarium.json' }, t('export.json')))),
    write ? h('section.card', h('h2', t('restore.title')), h('p.muted', t('restore.intro')),
      field(t('import.file'), restoreFile),
      h('button.btn.danger', {
        type: 'button', onclick: async () => {
          if (!restoreFile.files[0]) return;
          if (!(await confirmDialog(t('restore.confirm')))) return;
          const fd = new FormData();
          fd.append('file', restoreFile.files[0]);
          fd.append('confirm', 'REPLACE');
          try { await api('/import/restore', { method: 'POST', form: fd }); toast(t('restore.done'), 'success'); } catch (err) { errorToast(err); }
        },
      }, t('restore.submit'))) : null);
}

// ------------------------------------------------------------------ users (owners)

async function users(body) {
  const list = await api('/users');
  const me = state.session.user;
  const reload = () => users(body);

  async function addUser() {
    const form = h('form.stack',
      field(t('login.username'), h('input', { name: 'username', required: true, pattern: '[A-Za-z0-9._@\\-]{2,64}', autocomplete: 'off' })),
      field(t('account.display_name'), h('input', { name: 'display_name' })),
      field(t('users.role'), h('select', { name: 'role' }, ['reader', 'owner'].map((r) => h('option', { value: r }, t(`role.${r}`)))), t('users.role_hint')),
      field(t('login.password'), h('input', { name: 'password', type: 'password', minlength: 8, required: true, autocomplete: 'new-password' })));
    const ok = await dialog(t('users.add'), form, [
      { label: t('action.cancel'), value: false },
      {
        label: t('action.add'), kind: 'primary', onclick: async () => {
          if (!form.reportValidity()) return false;
          try { await api('/users', { method: 'POST', body: formData(form) }); return true; } catch (err) { errorToast(err); return false; }
        },
      },
    ]);
    if (ok) reload();
  }

  async function resetPassword(u) {
    const form = h('form', field(t('account.new_password'), h('input', { name: 'password', type: 'password', minlength: 8, required: true, autocomplete: 'new-password' })));
    const ok = await dialog(t('users.reset', { name: u.username }), form, [
      { label: t('action.cancel'), value: false },
      {
        label: t('action.save'), kind: 'primary', onclick: async () => {
          if (!form.reportValidity()) return false;
          try { await api(`/users/${u.id}`, { method: 'PATCH', body: formData(form) }); return true; } catch (err) { errorToast(err); return false; }
        },
      },
    ]);
    if (ok) toast(t('common.saved'), 'success');
  }

  clear(body,
    h('div.card-head', h('p.muted', t('users.intro')), h('button.btn.primary', { type: 'button', onclick: addUser }, icon('add'), ' ', t('users.add'))),
    h('ul.list.cards', list.map((u) => h('li.list-row',
      h('span.grow', h('strong', u.display_name || u.username), u.display_name ? h('small.muted', ` @${u.username}`) : null,
        u.id === me.id ? h('small.badge', t('users.you')) : null,
        h('small.block.muted', t('users.last_login', { date: u.last_login_at ? fmtDate(u.last_login_at) : '—' }))),
      h('select', {
        'aria-label': t('users.role'), disabled: u.id === me.id,
        onchange: async (e) => {
          try { await api(`/users/${u.id}`, { method: 'PATCH', body: { role: e.target.value } }); toast(t('common.saved'), 'success'); } catch (err) { errorToast(err); reload(); }
        },
      }, ['owner', 'reader'].map((r) => h('option', { value: r, selected: u.role === r }, t(`role.${r}`)))),
      h('div.row-actions',
        h('button.btn.small', { type: 'button', onclick: () => resetPassword(u) }, t('users.reset_short')),
        u.id !== me.id ? h('button.icon-btn', {
          type: 'button', 'aria-label': t('action.delete'), title: t('action.delete'), onclick: async () => {
            if (!(await confirmDialog(t('users.delete_confirm', { name: u.username })))) return;
            try { await api(`/users/${u.id}`, { method: 'DELETE' }); reload(); } catch (err) { errorToast(err); }
          },
        }, icon('trash')) : null)))));
}

// ------------------------------------------------------------------ API tokens + MCP

async function tokens(body) {
  const list = await api('/tokens');
  const reload = () => tokens(body);
  const mcpUrl = `${location.origin}/mcp`;

  async function create() {
    const form = h('form.stack',
      field(t('tokens.name'), h('input', { name: 'name', required: true, placeholder: 'hermes, claude…' })),
      field(t('tokens.scope'), h('select', { name: 'scope' },
        h('option', { value: 'read' }, t('tokens.scope.read')),
        canWrite() ? h('option', { value: 'write' }, t('tokens.scope.write')) : null), t('tokens.scope_hint')));
    const created = await dialog(t('tokens.create'), form, [
      { label: t('action.cancel'), value: null },
      {
        label: t('tokens.create'), kind: 'primary', onclick: async () => {
          if (!form.reportValidity()) return false;
          try { return await api('/tokens', { method: 'POST', body: formData(form) }); } catch (err) { errorToast(err); return false; }
        },
      },
    ]);
    if (!created) return;
    const config = JSON.stringify({ mcpServers: { cellarium: { url: mcpUrl, headers: { Authorization: `Bearer ${created.token}` } } } }, null, 2);
    await dialog(t('tokens.created'), h('div.stack',
      h('p', t('tokens.copy_now')),
      copyBox(created.token),
      h('p.muted.small', t('tokens.mcp_config')),
      copyBox(config, true)), [{ label: t('action.close'), value: true }]);
    reload();
  }

  clear(body,
    h('section.card', h('h2', t('tokens.mcp_title')), h('p.muted', t('tokens.mcp_intro')),
      h('dl.facts', h('dt', t('tokens.endpoint')), h('dd', h('code', mcpUrl)), h('dt', t('tokens.auth')), h('dd', h('code', 'Authorization: Bearer cel_…'))),
      h('p.muted.small', t('tokens.guide_hint'))),
    h('div.card-head', h('h2', t('settings.tokens')), h('button.btn.primary', { type: 'button', onclick: create }, icon('add'), ' ', t('tokens.create'))),
    list.length ? h('ul.list.cards', list.map((tok) => h('li.list-row',
      h('span.grow', h('strong', tok.name), ' ', h(`small.badge.scope-${tok.scope}`, t(`tokens.scope.${tok.scope}`)),
        h('small.block.muted', `${tok.prefix}… · ${tok.username} · ${t('tokens.last_used', { date: tok.last_used_at ? fmtDate(tok.last_used_at) : '—' })}`)),
      h('button.btn.small.danger', {
        type: 'button', onclick: async () => {
          if (!(await confirmDialog(t('tokens.revoke_confirm', { name: tok.name })))) return;
          try { await api(`/tokens/${tok.id}`, { method: 'DELETE' }); reload(); } catch (err) { errorToast(err); }
        },
      }, t('tokens.revoke'))))) : emptyState(t('tokens.none')));
}

function copyBox(text, multiline = false) {
  const area = h(multiline ? 'textarea.code' : 'input.code', { readOnly: true, value: text, rows: multiline ? 8 : null, onclick: (e) => e.target.select() });
  if (multiline) area.textContent = text;
  return h('div.copy-box', area, h('button.btn.small', {
    type: 'button', onclick: async () => {
      try { await navigator.clipboard.writeText(text); toast(t('common.copied'), 'success'); } catch { area.select(); }
    },
  }, t('action.copy')));
}

// ------------------------------------------------------------------ own account

async function account(body) {
  const user = state.session.user;
  const form = h('form.stack', {
    onsubmit: async (e) => {
      e.preventDefault();
      const data = formData(form);
      if (data.password !== data.password2) { toast(t('account.password_mismatch'), 'error'); return; }
      try {
        await api('/password', { method: 'POST', body: { current: data.current, password: data.password } });
        form.reset();
        toast(t('account.password_changed'), 'success');
      } catch (err) { errorToast(err); }
    },
  },
  field(t('account.current_password'), h('input', { name: 'current', type: 'password', required: true, autocomplete: 'current-password' })),
  field(t('account.new_password'), h('input', { name: 'password', type: 'password', minlength: 8, required: true, autocomplete: 'new-password' })),
  field(t('account.password_repeat'), h('input', { name: 'password2', type: 'password', minlength: 8, required: true, autocomplete: 'new-password' })),
  h('button.btn.primary', { type: 'submit' }, t('account.change_password')));

  const lang = h('select', {
    onchange: async (e) => { await loadLang(e.target.value); await refreshSession(); route(); },
  }, Object.entries(LANGS).map(([code, label]) => h('option', { value: code, selected: code === state.lang }, label)));

  clear(body,
    h('section.card', h('h2', user.display_name || user.username), h('p.muted', `@${user.username} · ${t(`role.${user.role}`)}`)),
    h('section.card', h('h2', t('settings.language')), field(t('settings.language'), lang)),
    h('section.card', h('h2', t('account.change_password')), form));
}

// ------------------------------------------------------------------ wine types

async function types(body) {
  const write = isOwner() && canWrite();
  const list = await api('/types');
  const form = h('form.grid-2', {
    onsubmit: async (e) => {
      e.preventDefault();
      try { await api('/types', { method: 'POST', body: formData(form) }); state.types = await api('/types'); types(body); } catch (err) { errorToast(err); }
    },
  },
  field(t('types.code'), h('input', { name: 'code', required: true, pattern: '[a-z][a-z0-9_\\-]{1,30}', placeholder: 'orange' })),
  field(t('types.color'), h('input', { name: 'color', type: 'color', value: '#e08a3c' })),
  field(t('types.label_fr'), h('input', { name: 'label_fr', required: true, placeholder: 'Orange' })),
  field(t('types.label_en'), h('input', { name: 'label_en', required: true, placeholder: 'Orange' })),
  h('button.btn.primary', { type: 'submit' }, t('action.add')));
  clear(body,
    h('section.card', h('h2', t('settings.types')), h('p.muted', t('types.intro')),
      h('ul.list', list.map((ty) => h('li.list-row', typeDot(ty.code), h('span.grow', typeLabel(ty.code), h('small.muted', ` ${ty.code}`)),
        write && !ty.builtin ? h('button.icon-btn', {
          type: 'button', 'aria-label': t('action.delete'), onclick: async () => {
            try { await api(`/types/${ty.code}`, { method: 'DELETE' }); state.types = await api('/types'); types(body); } catch (err) { errorToast(err); }
          },
        }, icon('trash')) : null)))),
    write ? h('section.card', h('h2', t('types.add')), form) : null);
}

// ------------------------------------------------------------------ about

async function about(body) {
  clear(body, h('section.card',
    h('h2', 'Cellarium'),
    h('p', t('about.text')),
    h('dl.facts',
      h('dt', t('about.version')), h('dd', state.session.version),
      h('dt', t('about.api')), h('dd', h('a', { href: '/api/docs', target: '_blank' }, '/api/docs')),
      h('dt', t('about.source')), h('dd', h('a', { href: 'https://github.com/dukyduke/cellarium', target: '_blank', rel: 'noopener' }, 'github.com/dukyduke/cellarium')),
      h('dt', t('about.license')), h('dd', 'MIT'))));
}
