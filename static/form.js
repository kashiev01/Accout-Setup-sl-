// New request form. Mirrors intake.build_rows() checks for instant feedback;
// the server re-validates everything on Create.
const ICON_CHECK = '<svg class="check" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#30D9B7" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M7.5 12.5l3 3 6-6.5"/></svg>';
const ICON_TICK = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
const ICON_PLUS = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#6f6f6f" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 6v12M6 12h12"/></svg>';
const ICON_X = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';

const LICENSE_LABELS = { trial: 'Trial', paid: 'Paid', partner: 'Partner', internal: 'Internal' };
const MODE_LABELS = { quicklink: 'QuickLink', dashboard_manager: 'Dashboard Manager', osint_start: 'OSINT Start' };
const QUICK = [[7, '7 days'], [14, '14 days'], [30, '30 days'], [90, '3 months']];
const POINT_CHIPS = ['500', '1000', '5000'];

let META = null;
let TODAY = '';
const S = {
  mode: 'form',
  product: 'Crimewall', license: 'trial',
  start: '', end: '',
  company: '', hubspot: '',
  packs: {},        // category -> template id
  packDates: {},    // template id -> {start, end}
  customDates: false,
  modes: new Set(),
  points: '500',
  users: [{ name: '', email: '' }],
  notes: '',
};

// ── helpers ──────────────────────────────────────────────────────────────────
const pad = (x) => String(x).padStart(2, '0');
function normDate(raw) {
  const t = (raw || '').trim();
  let m;
  if ((m = t.match(/^(\d{4})[-./](\d{1,2})[-./](\d{1,2})$/))) return `${m[1]}-${pad(m[2])}-${pad(m[3])}`;
  if ((m = t.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})$/))) return `${m[3]}-${pad(m[2])}-${pad(m[1])}`;
  if ((m = t.match(/^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$/))) {
    const [a, b] = [Number(m[1]), Number(m[2])];
    return a > 12 ? `${m[3]}-${pad(b)}-${pad(a)}` : `${m[3]}-${pad(a)}-${pad(b)}`;
  }
  return t;
}
const isDate = (d) => /^\d{4}-\d{2}-\d{2}$/.test(d) && !isNaN(new Date(d + 'T00:00:00Z')) && new Date(d + 'T00:00:00Z').toISOString().slice(0, 10) === d;
function addDays(d, n) { const x = new Date(d + 'T00:00:00Z'); x.setUTCDate(x.getUTCDate() + n); return x.toISOString().slice(0, 10); }
const daysBetween = (a, b) => Math.round((new Date(b) - new Date(a)) / 864e5);
const isEmail = (e) => /^[A-Za-z0-9._%+'\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$/.test((e || '').trim());
function deriveName(email) {
  return email.split('@')[0].split(/[._\-+]+/).filter(Boolean).map((w) => w[0].toUpperCase() + w.slice(1).toLowerCase()).join(' ');
}
const hubId = (raw) => ((raw || '').trim().match(/(\d{6,})\/?\s*$/) || [])[1] || '';
const isCW = () => S.product === 'Crimewall';
const productMeta = () => META.products[S.product];

function shortLabels(templates) {
  // Drop the words every template in a category starts with ("API DN Leaks ...").
  const words = templates.map((t) => t.label.split(' '));
  let n = 0;
  while (words.every((w) => w.length > n + 1 && w[n] === words[0][n])) n++;
  return templates.map((t, i) => ({ ...t, short: words[i].slice(n).join(' ') }));
}
function templateById(tid) {
  for (const p of Object.values(META.products)) for (const c of p.categories) {
    const t = c.templates.find((x) => x.id === tid);
    if (t) return { ...t, category: c.id };
  }
  return null;
}

// ── renderers ────────────────────────────────────────────────────────────────
function renderProducts() {
  $('#products').innerHTML = Object.keys(META.products).map((p) => `
    <button type="button" class="product${S.product === p ? ' on' : ''}" data-product="${esc(p)}" aria-pressed="${S.product === p}">
      <span class="t">${esc(p)}${ICON_CHECK}</span>
    </button>`).join('');
  $('#licenses').innerHTML = productMeta().licenses.map((l) =>
    `<button type="button" class="chip${S.license === l ? ' on' : ''}" data-license="${l}">${LICENSE_LABELS[l] || l}</button>`).join('');
  $('#modes-card').hidden = !isCW();
  $('#points-card').hidden = isCW();
  $('#company-req').textContent = isCW() ? ' *' : ' (optional)';
}

function renderPeriod() {
  const days = isDate(S.start) && isDate(S.end) ? daysBetween(S.start, S.end) : null;
  $('#quick').innerHTML = QUICK.map(([n, l]) =>
    `<button type="button" class="chip sm${days === n ? ' on' : ''}" data-days="${n}">${l}</button>`).join('');
  if ($('#start') !== document.activeElement) $('#start').value = S.start;
  if ($('#end') !== document.activeElement) $('#end').value = S.end;
  const startBad = !!S.start && !isDate(S.start);
  const endBad = !!S.end && !isDate(S.end);
  const endBefore = !startBad && !endBad && S.start && S.end && S.end <= S.start;
  $('#start-err').hidden = !startBad;
  $('#start').classList.toggle('bad', startBad);
  $('#end-err').hidden = !(endBad || endBefore);
  $('#end-err').textContent = endBad ? 'Format: YYYY-MM-DD, e.g. 2026-10-27' : 'End date must be after the start date';
  $('#end').classList.toggle('bad', endBad || endBefore);
}

function renderCompany() {
  const id = hubId(S.hubspot);
  const hint = $('#hub-hint');
  hint.className = 'hint' + (id ? ' ok' : '');
  hint.textContent = id ? `Deal ID: ${id}` : (S.hubspot.trim() ? 'No ID found — paste the deal link or the number' : 'Paste the deal link — the ID is taken from it');
}

function renderPacks() {
  $('#packs').innerHTML = productMeta().categories.map((c) => {
    const cur = S.packs[c.id] || '';
    const chips = [{ id: '', short: 'None', label: 'No pack' }, ...shortLabels(c.templates)].map((t) =>
      `<button type="button" class="chip sm${cur === t.id ? ' on' : ''}" title="${esc(t.label)}" data-cat="${c.id}" data-tid="${t.id}">${esc(t.short)}</button>`).join('');
    return `<div class="pack-row"><div class="name"><span>${esc(c.title)}</span>${c.hint ? `<span class="sub">${esc(c.hint)}</span>` : ''}</div><div class="chips">${chips}</div></div>`;
  }).join('');
  renderCustomDates();
}

function renderCustomDates() {
  $('#custom-dates-btn').textContent = S.customDates ? 'Hide custom pack dates' : 'Custom dates for individual packs…';
  const box = $('#custom-dates');
  box.hidden = !S.customDates;
  if (!S.customDates) return;
  const chosen = Object.values(S.packs).filter(Boolean);
  if (!chosen.length) { box.innerHTML = '<span class="hint">Pick some packs above first.</span>'; return; }
  box.innerHTML = chosen.map((tid) => {
    const t = templateById(tid);
    const d = S.packDates[tid] || {};
    return `<div class="date-row"><span>${esc(t.label)}</span>
      <input class="input" data-pdate="start" data-tid="${tid}" aria-label="${esc(t.label)} start" placeholder="${esc(S.start || 'YYYY-MM-DD')}" value="${esc(d.start || '')}">
      <span class="sub">→</span>
      <input class="input" data-pdate="end" data-tid="${tid}" aria-label="${esc(t.label)} end" placeholder="${esc(S.end || 'YYYY-MM-DD')}" value="${esc(d.end || '')}"></div>`;
  }).join('') + '<span class="hint">Leave empty to use the request period.</span>';
}

function renderModes() {
  $('#modes').innerHTML = META.modes.map((m) => {
    const on = S.modes.has(m);
    return `<button type="button" class="chip${on ? ' on' : ''}" data-mode="${m}" aria-pressed="${on}" style="padding-left:12px">${on ? ICON_TICK : ICON_PLUS}${MODE_LABELS[m] || m}</button>`;
  }).join('');
}

function renderPoints() {
  $('#point-chips').innerHTML = POINT_CHIPS.map((v) =>
    `<button type="button" class="chip${S.points === v ? ' on' : ''}" data-points="${v}">${v}</button>`).join('');
  if ($('#points') !== document.activeElement) $('#points').value = S.points;
}

function renderUsers() {
  $('#users').innerHTML = S.users.map((u, i) => `
    <div class="user-row" data-i="${i}">
      <span class="n">${i + 1}</span>
      <div class="field name"><input class="input" data-k="name" aria-label="Full name" placeholder="First Last" value="${esc(u.name)}"><span class="hint warn" data-h="name" hidden></span></div>
      <div class="field email"><input class="input" type="email" data-k="email" aria-label="Email" placeholder="name@company.com" value="${esc(u.email)}"><span class="hint err" data-h="email" hidden>Check this email</span></div>
      <button type="button" class="icon-btn" data-remove="${i}" aria-label="Remove user ${i + 1}">${ICON_X}</button>
    </div>`).join('');
  S.users.forEach((_, i) => renderUserHints(i));
}

function renderUserHints(i) {
  const row = $(`.user-row[data-i="${i}"]`);
  if (!row) return;
  const u = S.users[i];
  const nameHint = $('[data-h="name"]', row);
  const derived = !u.name.trim() && isEmail(u.email);
  nameHint.hidden = !derived;
  nameHint.textContent = derived ? `Name will be taken from email: ${deriveName(u.email.trim())}` : '';
  const bad = !!u.email.trim() && !isEmail(u.email);
  $('[data-h="email"]', row).hidden = !bad;
  $('[data-k="email"]', row).classList.toggle('bad', bad);
}

// ── validation + summary ─────────────────────────────────────────────────────
function check() {
  const errors = [], warnings = [];
  if (S.mode === 'csv') {
    if (!$('#csv-text').value.trim()) errors.push('Choose a CSV file or paste its contents');
    return { errors, warnings };
  }
  if (!isDate(S.start) || !isDate(S.end)) errors.push('Enter start and end dates as YYYY-MM-DD');
  else if (S.end <= S.start) errors.push('End date must be after the start date');
  if (isCW() && !S.company.trim()) errors.push('Enter the company name');
  if (S.hubspot.trim() && !hubId(S.hubspot)) errors.push('No HubSpot ID found in the HubSpot field');
  const users = S.users.filter((u) => u.name.trim() || u.email.trim());
  if (!users.length) errors.push('Add at least one user');
  const bad = S.users.map((u, i) => [u, i]).filter(([u]) => (u.name.trim() || u.email.trim()) && !isEmail(u.email));
  if (bad.length) errors.push('Invalid email: ' + bad.map(([, i]) => '#' + (i + 1)).join(', '));
  const seen = new Set();
  for (const u of users) {
    const e = u.email.trim().toLowerCase();
    if (e && seen.has(e)) errors.push(`${u.email.trim()} is listed twice`);
    seen.add(e);
  }
  if (!isCW() && !/^\d+$/.test(S.points)) errors.push('API credits must be a whole number');
  for (const tid of Object.values(S.packs).filter(Boolean)) {
    const d = S.packDates[tid];
    if (d && (d.start || d.end)) {
      const t = templateById(tid);
      if (!isDate(d.start || '') || !isDate(d.end || '')) errors.push(`Custom dates for ${t.label}: both must be YYYY-MM-DD`);
      else if (d.end <= d.start) errors.push(`Custom dates for ${t.label}: end must be after start`);
    }
  }
  const derived = users.filter((u) => !u.name.trim() && isEmail(u.email)).length;
  if (derived) warnings.push(`${derived} ${derived === 1 ? 'name' : 'names'} will be taken from email — please check`);
  if (!hubId(S.hubspot)) warnings.push('Without a HubSpot deal ID the pack key gets company_id = 0');
  return { errors, warnings };
}

function renderSummary() {
  const { errors, warnings } = check();
  let rows;
  if (S.mode === 'csv') {
    const lines = $('#csv-text').value.trim().split(/\r?\n/).filter((l) => l.trim());
    rows = [['Source', 'CSV upload'], ['Rows', lines.length > 1 ? String(lines.length - 1) : '—']];
  } else {
    const days = isDate(S.start) && isDate(S.end) && S.end > S.start ? daysBetween(S.start, S.end) : null;
    const chosen = Object.values(S.packs).filter(Boolean).map((tid) => templateById(tid).label);
    const n = S.users.filter((u) => isEmail(u.email)).length;
    const id = hubId(S.hubspot);
    rows = [
      ['Product and license', `${S.product} · ${LICENSE_LABELS[S.license] || S.license}`],
      ['Period', days ? `${fmtDate(S.start)} → ${fmtDate(S.end)} · ${days} days` : '—'],
      ['Company', S.company.trim() || '—'],
      ['Deal ID', id || '—'],
      ['Packs', chosen.length ? chosen.join(', ') : 'No packs'],
      isCW() ? ['Modes', S.modes.size ? [...S.modes].map((m) => MODE_LABELS[m]).join(', ') : 'No modes'] : ['API credits', S.points || '—'],
      ['Will be created', `${n} ${isCW() ? (n === 1 ? 'CrimeWall account' : 'CrimeWall accounts') : (n === 1 ? 'SL API user' : 'SL API users')}${chosen.length ? ' + pack key' : ''}`],
    ];
  }
  $('#summary').innerHTML = rows.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('');
  setList('#errors', errors);
  setList('#warnings', warnings);
  $('#create').disabled = errors.length > 0;
}

function setList(sel, items) {
  const el = $(sel);
  el.hidden = !items.length;
  el.innerHTML = items.map((x) => `<li>${esc(x)}</li>`).join('');
}

function renderAll() {
  renderProducts(); renderPeriod(); renderCompany(); renderPacks(); renderModes(); renderPoints(); renderUsers();
  $('#company').value = S.company;
  $('#hubspot').value = S.hubspot;
  renderSummary();
}

// ── request <-> state ────────────────────────────────────────────────────────
function toRequest() {
  return {
    product: S.product,
    license_type: S.license,
    start_date: S.start, end_date: S.end,
    company: S.company.trim(), hubspot: S.hubspot.trim(),
    packs: Object.values(S.packs).filter(Boolean).map((tid) => ({ template: tid, start: S.packDates[tid]?.start || '', end: S.packDates[tid]?.end || '' })),
    modes: isCW() ? [...S.modes] : [],
    points: isCW() ? '' : S.points,
    users: S.users.filter((u) => u.name.trim() || u.email.trim()).map((u) => ({ name: u.name.trim(), email: u.email.trim() })),
    notes: S.notes,
  };
}

function loadRequest(req) {
  S.mode = 'form';
  S.product = META.products[req.product] ? req.product : 'Crimewall';
  const lics = productMeta().licenses;
  S.license = lics.includes((req.license_type || '').toLowerCase()) ? req.license_type.toLowerCase() : 'trial';
  S.start = normDate(req.start_date || '') || TODAY;
  S.end = normDate(req.end_date || '') || addDays(S.start, 7);
  S.company = req.company || '';
  S.hubspot = req.hubspot || req.company_id || '';
  S.packs = {}; S.packDates = {};
  for (const p of req.packs || []) {
    const t = templateById(String(p.template));
    if (!t) continue;
    S.packs[t.category] = t.id;
    if (p.start || p.end) { S.packDates[t.id] = { start: p.start || '', end: p.end || '' }; S.customDates = true; }
  }
  S.modes = new Set((req.modes || []).filter((m) => META.modes.includes(m)));
  S.points = String(req.points || '500');
  S.users = (req.users && req.users.length) ? req.users.map((u) => ({ name: u.name || '', email: u.email || '' })) : [{ name: '', email: '' }];
  S.notes = req.notes || '';
  setMode('form');
  renderAll();
}

function parseUserLines(text) {
  const out = [];
  for (const line of text.split(/\n/)) {
    const m = line.match(/[A-Za-z0-9._%+'\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}/);
    if (!m) continue;
    const before = line.slice(0, m.index).replace(/^[\s\-•*\d.)]+/, '').replace(/[\s:<\-–—(]+$/, '');
    const name = before.split(/\s[-–—]\s|:/).pop().trim();
    out.push({ name, email: m[0] });
  }
  return out;
}

function setMode(mode) {
  S.mode = mode;
  $('#form-mode').hidden = mode !== 'form';
  $('#csv-mode').hidden = mode !== 'csv';
  $('#mode-btn').textContent = mode === 'form' ? 'Upload CSV instead' : 'Use the form instead';
  renderSummary();
}

// ── create ───────────────────────────────────────────────────────────────────
async function create() {
  const { errors } = check();
  if (errors.length) return;
  const btn = $('#create');
  btn.disabled = true;
  btn.textContent = 'CREATING…';
  // Open the tab synchronously (inside the click) so popup blockers allow it.
  const tab = window.open('', '_blank');
  const body = S.mode === 'csv'
    ? { csv: $('#csv-text').value }
    : toRequest();
  const { ok, data } = await api(S.mode === 'csv' ? '/api/requests/csv' : '/api/requests', { method: 'POST', body });
  btn.textContent = 'CREATE';
  btn.disabled = false;
  if (!ok) {
    if (tab) tab.close();
    setList('#errors', (data && (data.errors || data.detail?.errors)) || ['The server rejected the request']);
    return;
  }
  if (tab) tab.location = data.url;
  else window.location = data.url;
  const created = $('#created');
  created.hidden = false;
  created.innerHTML = `<li>Request #${data.id} created — <a href="${esc(data.url)}" target="_blank" rel="noopener">open it</a></li>`;
}

// ── events ───────────────────────────────────────────────────────────────────
function bind() {
  document.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.product) {
      S.product = b.dataset.product; S.packs = {}; S.packDates = {};
      if (!productMeta().licenses.includes(S.license)) S.license = 'trial';
      renderProducts(); renderPacks(); renderSummary();
    } else if (b.dataset.license) {
      S.license = b.dataset.license; renderProducts(); renderSummary();
    } else if (b.dataset.days) {
      if (!isDate(S.start)) S.start = TODAY;
      S.end = addDays(S.start, Number(b.dataset.days)); renderPeriod(); renderCustomDates(); renderSummary();
    } else if (b.dataset.cat !== undefined && b.dataset.tid !== undefined) {
      S.packs[b.dataset.cat] = b.dataset.tid; renderPacks(); renderSummary();
    } else if (b.dataset.mode) {
      S.modes.has(b.dataset.mode) ? S.modes.delete(b.dataset.mode) : S.modes.add(b.dataset.mode);
      renderModes(); renderSummary();
    } else if (b.dataset.points) {
      S.points = b.dataset.points; renderPoints(); renderSummary();
    } else if (b.dataset.remove !== undefined) {
      S.users.splice(Number(b.dataset.remove), 1);
      if (!S.users.length) S.users.push({ name: '', email: '' });
      renderUsers(); renderSummary();
    }
  });

  $('#start-today').onclick = () => {
    const len = isDate(S.start) && isDate(S.end) && S.end > S.start ? daysBetween(S.start, S.end) : 7;
    S.start = TODAY; S.end = addDays(TODAY, len); renderPeriod(); renderSummary();
  };
  for (const id of ['start', 'end']) {
    $('#' + id).addEventListener('input', (e) => { S[id] = e.target.value.trim(); renderPeriod(); renderSummary(); });
    $('#' + id).addEventListener('blur', (e) => { S[id] = normDate(e.target.value); renderPeriod(); renderCustomDates(); renderSummary(); });
  }
  $('#company').addEventListener('input', (e) => { S.company = e.target.value; renderSummary(); });
  $('#hubspot').addEventListener('input', (e) => { S.hubspot = e.target.value; renderCompany(); renderSummary(); });
  $('#points').addEventListener('input', (e) => { S.points = e.target.value.replace(/\D/g, ''); e.target.value = S.points; renderPoints(); renderSummary(); });
  $('#custom-dates-btn').onclick = () => { S.customDates = !S.customDates; renderCustomDates(); };
  $('#custom-dates').addEventListener('input', (e) => {
    const el = e.target.closest('[data-pdate]');
    if (!el) return;
    S.packDates[el.dataset.tid] = { ...(S.packDates[el.dataset.tid] || {}), [el.dataset.pdate]: el.value.trim() };
    renderSummary();
  });
  $('#custom-dates').addEventListener('focusout', (e) => {
    const el = e.target.closest('[data-pdate]');
    if (!el) return;
    el.value = normDate(el.value);
    S.packDates[el.dataset.tid][el.dataset.pdate] = el.value;
    renderSummary();
  });
  $('#users').addEventListener('input', (e) => {
    const row = e.target.closest('.user-row');
    if (!row || !e.target.dataset.k) return;
    S.users[Number(row.dataset.i)][e.target.dataset.k] = e.target.value;
    renderUserHints(Number(row.dataset.i)); renderSummary();
  });
  $('#add-user').onclick = () => {
    S.users.push({ name: '', email: '' }); renderUsers(); renderSummary();
    $$('.user-row').pop().querySelector('input').focus();
  };
  $('#paste-users-btn').onclick = () => { $('#paste-users').hidden = !$('#paste-users').hidden; $('#paste-users-text').focus(); };
  $('#paste-users-add').onclick = () => {
    const found = parseUserLines($('#paste-users-text').value);
    const kept = S.users.filter((u) => u.name.trim() || u.email.trim());
    S.users = kept.concat(found);
    if (!S.users.length) S.users = [{ name: '', email: '' }];
    $('#paste-users-text').value = ''; $('#paste-users').hidden = true;
    renderUsers(); renderSummary();
  };
  $('#create').onclick = create;

  $('#mode-btn').onclick = () => setMode(S.mode === 'form' ? 'csv' : 'form');
  $('#csv-text').addEventListener('input', renderSummary);
  $('#csv-file').addEventListener('change', async (e) => {
    const f = e.target.files[0];
    if (f) { $('#csv-text').value = await f.text(); renderSummary(); }
  });

  const modal = $('#paste-modal');
  $('#paste-request-btn').onclick = () => { modal.hidden = false; $('#paste-errors').hidden = true; $('#paste-request-text').focus(); };
  $('#paste-cancel').onclick = () => { modal.hidden = true; };
  modal.addEventListener('click', (e) => { if (e.target === modal) modal.hidden = true; });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !modal.hidden) modal.hidden = true; });
  $('#paste-apply').onclick = async () => {
    const { ok, data } = await api('/api/requests/parse', { method: 'POST', body: { text: $('#paste-request-text').value } });
    if (!ok) { setList('#paste-errors', ['Could not read this request']); return; }
    loadRequest(data);
    modal.hidden = true;
    $('#paste-request-text').value = '';
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };
}

// ── init ─────────────────────────────────────────────────────────────────────
(async function init() {
  META = await showUser();
  if (!META) { document.body.insertAdjacentHTML('afterbegin', '<p class="alert err">Could not reach the server.</p>'); return; }
  TODAY = META.today;
  S.start = TODAY;
  S.end = addDays(TODAY, 7);
  bind();
  renderAll();
  const from = new URLSearchParams(location.search).get('from');
  if (from) {
    const { ok, data } = await api(`/api/jobs/${encodeURIComponent(from)}`);
    if (ok && data.request) loadRequest(data.request);
  }
})();
