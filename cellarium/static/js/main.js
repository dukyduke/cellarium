// Entry point: session bootstrap, layout (top bar + mobile tab bar) and hash router.
import { api, clear, errorToast, h, icon, LANGS, loadLang, state, t } from './core.js';
import { renderLogin, renderSetup } from './pages/login.js';
import { renderSummary } from './pages/summary.js';
import { renderCellar } from './pages/cellar.js';
import { renderWine, renderNewWine } from './pages/wine.js';
import { renderSettings } from './pages/settings.js';

const routes = [
  [/^#\/login$/, renderLogin, { public: true }],
  [/^#\/setup$/, renderSetup, { public: true }],
  [/^#\/?$/, renderSummary],
  [/^#\/cave(?:\/(list|schedule|plan|history))?$/, (m, q, el) => renderCellar(m[1] || 'list', q, el)],
  [/^#\/wine\/new$/, (m, q, el) => renderNewWine(q, el)],
  [/^#\/wine\/(\d+)$/, (m, q, el) => renderWine(Number(m[1]), q, el)],
  [/^#\/settings(?:\/(\w+))?$/, (m, q, el) => renderSettings(m[1] || 'storages', el)],
];

const main = () => document.getElementById('app');

export async function refreshSession() {
  state.session = await api('/session');
  if (state.session.authenticated) state.types = await api('/types');
  return state.session;
}

function navLink(href, iconName, label, match) {
  const active = match.test(location.hash || '#/');
  return h('a.nav-link', { href, class: active ? 'active' : '', 'aria-current': active ? 'page' : null },
    icon(iconName), h('span', label));
}

function renderChrome() {
  const header = document.getElementById('topbar');
  const tabbar = document.getElementById('tabbar');
  const user = state.session?.user;
  if (!user) {
    clear(header, h('a.brand', { href: '#/' }, h('img', { src: '/static/logo.svg', alt: '' }), 'Cellarium'), langSwitch());
    clear(tabbar);
    tabbar.hidden = true;
    return;
  }
  const links = () => [
    navLink('#/', 'home', t('nav.summary'), /^#\/?$/),
    navLink('#/cave', 'cellar', t('nav.cellar'), /^#\/cave/),
    user.can_write ? navLink('#/wine/new', 'add', t('nav.add'), /^#\/wine\/new/) : null,
    navLink('#/settings', 'settings', t('nav.settings'), /^#\/settings/),
  ];
  clear(header,
    h('a.brand', { href: '#/' }, h('img', { src: '/static/logo.svg', alt: '' }), 'Cellarium'),
    h('nav.top-nav', { 'aria-label': t('nav.main') }, links()),
    h('div.top-tools',
      langSwitch(),
      h('span.user-chip', { title: t(`role.${user.role}`) }, user.display_name || user.username,
        user.role === 'reader' ? h('small.badge', t('role.reader')) : null),
      h('button.icon-btn', { type: 'button', title: t('action.logout'), 'aria-label': t('action.logout'), onclick: logout },
        icon('logout'))));
  clear(tabbar, links());
  tabbar.hidden = false;
}

function langSwitch() {
  return h('select.lang', {
    'aria-label': t('settings.language'),
    onchange: async (e) => { await loadLang(e.target.value); route(); },
  }, Object.entries(LANGS).map(([code, label]) => h('option', { value: code, selected: code === state.lang }, label)));
}

async function logout() {
  await api('/logout', { method: 'POST' });
  state.session = null;
  location.hash = '#/login';
}

export async function route() {
  const hash = location.hash || '#/';
  const [path, query = ''] = hash.split('?');
  const params = new URLSearchParams(query);
  const entry = routes.find(([re]) => re.test(path));
  if (!state.session) {
    try { await refreshSession(); } catch (err) { errorToast(err); }
  }
  if (state.session?.setup_required && path !== '#/setup') { location.hash = '#/setup'; return; }
  if (!entry) { location.hash = '#/'; return; }
  const [re, render, opts = {}] = entry;
  if (!opts.public && !state.session?.authenticated) { location.hash = '#/login'; return; }
  renderChrome();
  const el = main();
  el.setAttribute('aria-busy', 'true');
  try {
    await render(path.match(re), params, el);
  } catch (err) {
    errorToast(err);
    clear(el, h('div.empty', h('p', t('error.generic'))));
  } finally {
    el.removeAttribute('aria-busy');
  }
  document.title = `${document.querySelector('#app h1')?.textContent || ''} · Cellarium`.replace(/^ · /, '');
}

window.addEventListener('unhandledrejection', (e) => errorToast(e.reason));
window.addEventListener('hashchange', () => { window.scrollTo(0, 0); route(); });

(async () => {
  await loadLang(state.lang);
  await route();
})();

