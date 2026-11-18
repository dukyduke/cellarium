// Bottle-level dialogs shared by the plan and the wine sheet.
import { api, canWrite, dialog, errorToast, field, fmtPrice, fmtVolume, fmtYear, formData, h, locationLabel, t, toast, typeLabel } from '../core.js';

export async function outDialog(bottle) {
  const today = new Date().toISOString().slice(0, 10);
  const form = h('form.stack',
    field(t('bottle.out_status'), h('select', { name: 'status' },
      ['drunk', 'gifted', 'lost'].map((s) => h('option', { value: s }, t(`out.${s}`))))),
    field(t('bottle.out_date'), h('input', { type: 'date', name: 'date', value: today })),
    field(t('bottle.rating'), h('select', { name: 'rating' }, h('option', { value: '' }, '—'),
      [1, 2, 3, 4, 5].map((n) => h('option', { value: n }, '★'.repeat(n))))),
    field(t('bottle.out_note'), h('textarea', { name: 'note', rows: 2 })));
  return dialog(t('bottle.out_title', { name: bottle.name, year: fmtYear(bottle.year) }), form, [
    { label: t('action.cancel'), value: null },
    {
      label: t('bottle.out_confirm'), kind: 'primary', onclick: async () => {
        const data = formData(form);
        if (data.rating) data.rating = Number(data.rating);
        try {
          const res = await api(`/bottles/${bottle.id}/out`, { method: 'POST', body: data });
          toast(t('bottle.out_done'), 'success');
          return res;
        } catch (err) { errorToast(err); return false; }
      },
    },
  ]);
}

/** Details of one bottle with actions. Returns 'move' | 'out' | null. */
export async function bottleDialog(bottle) {
  const content = h('div.stack',
    h('p', h('strong', bottle.appellation || ''), bottle.appellation ? ' — ' : '', bottle.name),
    h('dl.facts',
      h('dt', t('col.year')), h('dd', fmtYear(bottle.year)),
      h('dt', t('col.type')), h('dd', typeLabel(bottle.type)),
      h('dt', t('col.volume')), h('dd', fmtVolume(bottle.volume_cl)),
      h('dt', t('col.price')), h('dd', fmtPrice(bottle.unit_price)),
      h('dt', t('bottle.location')), h('dd', locationLabel(bottle.location))));
  const actions = [{ label: t('bottle.open_sheet'), value: 'sheet' }];
  if (canWrite()) {
    actions.push({ label: t('bottle.move'), value: 'move' });
    actions.push({ label: t('bottle.take_out'), kind: 'primary', value: 'out' });
  }
  const choice = await dialog(t('bottle.title'), content, actions);
  if (choice === 'sheet') location.hash = `#/wine/${bottle.wine_id}`;
  if (choice === 'out') return (await outDialog(bottle)) ? 'out' : null;
  return choice;
}
