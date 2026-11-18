// Login and first-run setup (creation of the first owner account).
import { api, ApiError, clear, field, formData, h, state, t, toast } from '../core.js';
import { refreshSession } from '../main.js';

function card(title, intro, form) {
  return h('section.auth-card', h('img.auth-logo', { src: '/static/logo.svg', alt: '' }), h('h1', title),
    intro ? h('p.muted', intro) : null, form);
}

export async function renderLogin(_m, _q, el) {
  if (state.session?.authenticated) { location.hash = '#/'; return; }
  const error = h('p.form-error', { role: 'alert' });
  const form = h('form.stack', {
    onsubmit: async (e) => {
      e.preventDefault();
      error.textContent = '';
      try {
        await api('/login', { method: 'POST', body: formData(form) });
        await refreshSession();
        location.hash = '#/';
      } catch (err) {
        error.textContent = err instanceof ApiError && err.status === 429 ? t('login.throttled') : t('login.failed');
      }
    },
  },
  field(t('login.username'), h('input', { name: 'username', autocomplete: 'username', required: true, autofocus: true })),
  field(t('login.password'), h('input', { name: 'password', type: 'password', autocomplete: 'current-password', required: true })),
  error,
  h('button.btn.primary.block', { type: 'submit' }, t('login.submit')));
  clear(el, card(t('login.title'), t('login.intro'), form));
}

export async function renderSetup(_m, _q, el) {
  const session = await refreshSession();
  if (!session.setup_required) { location.hash = '#/login'; return; }
  const error = h('p.form-error', { role: 'alert' });
  const form = h('form.stack', {
    onsubmit: async (e) => {
      e.preventDefault();
      const data = formData(form);
      if (data.password !== data.password2) { error.textContent = t('account.password_mismatch'); return; }
      delete data.password2;
      try {
        await api('/setup', { method: 'POST', body: data });
        await refreshSession();
        toast(t('setup.done'), 'success');
        location.hash = '#/settings/storages';
      } catch (err) {
        error.textContent = err.message;
      }
    },
  },
  field(t('setup.code'), h('input', { name: 'code', required: true, autocomplete: 'off' }), t('setup.code_hint')),
  field(t('login.username'), h('input', { name: 'username', required: true, autocomplete: 'username', pattern: '[A-Za-z0-9._@\\-]{2,64}' })),
  field(t('account.display_name'), h('input', { name: 'display_name', autocomplete: 'name' })),
  field(t('login.password'), h('input', { name: 'password', type: 'password', minlength: 8, required: true, autocomplete: 'new-password' })),
  field(t('account.password_repeat'), h('input', { name: 'password2', type: 'password', minlength: 8, required: true, autocomplete: 'new-password' })),
  error,
  h('button.btn.primary.block', { type: 'submit' }, t('setup.submit')));
  clear(el, card(t('setup.title'), t('setup.intro'), form));
}
