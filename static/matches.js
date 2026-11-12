// Live preview for the "Enter matches" form. The server recalculates everything on submit.
(() => {
  const form = document.getElementById('matchForm');
  if (!form) return;
  const R = +form.dataset.round, BASE = +form.dataset.base;
  const countET = form.dataset.et === '1', countPen = form.dataset.pen === '1';
  const STEP = { win: 0, winET: -5000, winPen: -10000, lossPen: -20000, lossET: -25000, loss: -30000 };
  const LABEL = { win: 'Win', winET: 'Win after extra time', winPen: 'Win on penalties', lossPen: 'Loss on penalties', lossET: 'Loss after extra time', loss: 'Loss' };
  const nl = (x, d = 2) => x.toLocaleString('nl-NL', { minimumFractionDigits: d, maximumFractionDigits: d });
  const val = (row, k) => { const v = row.querySelector(`[name^="${k}_"]`).value; return v === '' ? null : Math.max(0, Math.floor(+v)); };
  const norm = s => (s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]/g, '');

  function compute(row) {
    const gf = val(row, 'gf'), ga = val(row, 'ga'), dec = row.querySelector('select').value;
    if (gf == null || ga == null) return { pending: 'Enter the score' };
    let key, pf = 0, pa = 0;
    if (dec === 'regular') { if (gf === ga) return { error: "A match decided in regular time can't end level." }; key = gf > ga ? 'win' : 'loss'; }
    if (dec === 'extra') { if (gf === ga) return { error: 'Level after extra time means penalties.' }; key = gf > ga ? 'winET' : 'lossET'; }
    if (dec === 'penalties') {
      if (gf !== ga) return { error: 'A shootout only happens after a level score.' };
      pf = val(row, 'pf'); pa = val(row, 'pa');
      if (pf == null || pa == null) return { pending: 'Enter the shootout score' };
      if (pf === pa) return { error: "A shootout can't end level." };
      key = pf > pa ? 'winPen' : 'lossPen';
    }
    let etf = 0, eta = 0;
    if (dec !== 'regular' && !countET) { etf = val(row, 'etf') || 0; eta = val(row, 'eta') || 0; if (etf > gf || eta > ga) return { error: 'More extra-time goals than goals in total.' }; }
    const gFor = gf - etf + (dec === 'penalties' && countPen ? pf : 0);
    const gAg = ga - eta + (dec === 'penalties' && countPen ? pa : 0);
    const base = BASE + STEP[key], pts = base + 501 * gFor - 500 * gAg;
    const n = +row.dataset.played, avg = (+row.dataset.avg * n + pts) / (n + 1);
    return { key, base, pts, avg, gFor, gAg, lost: key.startsWith('loss') };
  }

  function paint(row) {
    const dec = row.querySelector('select').value;
    row.querySelector('.pen').hidden = dec !== 'penalties';
    row.querySelector('.et').hidden = countET || dec === 'regular';
    const c = compute(row), out = row.querySelector('.mprev');
    if (c.pending) { out.innerHTML = `<span class="muted">${c.pending}</span>`; return false; }
    if (c.error) { out.innerHTML = `<span class="warn">${c.error}</span>`; return false; }
    out.innerHTML = `<span class="${c.lost ? 'warn' : 'okc'}">${LABEL[c.key]}</span>: ${nl(c.base, 0)} ${c.gFor ? `+ ${c.gFor}×501 ` : ''}${c.gAg ? `− ${c.gAg}×500 ` : ''}= <b>${nl(c.pts, 0)}</b> · new average <b>${nl(c.avg)}</b>${c.lost ? ' · <b class="warn">out of the cup</b>' : ''}`;
    return true;
  }

  // if two of your own teams meet, fill the other line the mirrored way
  function mirror(row) {
    const opp = row.querySelector('[name^="opp_"]').value;
    const other = [...form.querySelectorAll('.mrow')].find(r => r !== row && norm(r.dataset.name) === norm(opp));
    if (!other) return;
    const oOpp = other.querySelector('[name^="opp_"]');
    if (oOpp.value && norm(oOpp.value) !== norm(row.dataset.name)) return;
    const set = (k, v) => { other.querySelector(`[name^="${k}_"]`).value = v; };
    const get = k => row.querySelector(`[name^="${k}_"]`).value;
    oOpp.value = row.dataset.name;
    set('gf', get('ga')); set('ga', get('gf')); set('etf', get('eta')); set('eta', get('etf')); set('pf', get('pa')); set('pa', get('pf'));
    other.querySelector('select').value = row.querySelector('select').value;
    paint(other);
  }

  const rows = [...form.querySelectorAll('.mrow')];
  const btn = document.getElementById('publishMatches');
  const refresh = () => { btn.disabled = !rows.map(paint).every(Boolean); };
  form.addEventListener('input', e => { const row = e.target.closest('.mrow'); if (row) { paint(row); refresh(); } });
  form.addEventListener('change', e => { const row = e.target.closest('.mrow'); if (row) { mirror(row); refresh(); } });
  refresh();
})();
