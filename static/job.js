// Request page: polls the job while it runs, then shows emails, keys and the checklist.
const JOB_ID = location.pathname.split('/').filter(Boolean).pop();
const STATUS_TEXT = {
  queued: 'Queued', running: 'Running', done: 'Done', done_with_errors: 'Finished with errors', failed: 'Failed',
};
const CELL_TEXT = {
  queued: 'Queued', running: 'Working…', created: 'Created', updated: 'Updated', ok: 'Done',
  failed: 'Failed', skipped: 'Skipped', 'n/a': '—',
};
const PHASE_DESC = {
  '1': 'Create or update CrimeWall accounts / SL API users and tokens',
  '2': 'packs_api key and packs in Billing',
  '3': 'Write the pack key into each CrimeWall account’s “User keys”',
};
const FRAME_CSS = 'body{font-family:Arial,sans-serif;font-size:14px;color:#222;margin:0;padding:28px 32px;line-height:1.55}a{color:#1a73e8}ul{margin:4px 0;padding-left:24px}li{margin:4px 0}p{margin:6px 0}code{font-family:Consolas,monospace;background:#f3f3f3;padding:1px 4px;border-radius:3px}';

let job = null;
let emailIdx = 0;
let renderedEmail = -1;
let logOpen = true;

function cellFor(user, phase) {
  const st = user[`p${phase}`];
  return `<span class="tag ${esc(st)}">${esc(CELL_TEXT[st] || st)}</span>`;
}

function render() {
  const finished = !['queued', 'running'].includes(job.status);
  document.title = `#${job.id} ${job.title} · Account Setup`;
  $('#title').textContent = job.title;
  const by = job.created_by ? ` · by ${job.created_by}` : '';
  const src = job.source === 'slack' ? ' · from Slack' : job.source === 'csv' ? ' · CSV upload' : '';
  $('#meta').textContent = `Request #${job.id}${by}${src} · ${fmtDateTime(job.created_at)}${job.dry_run ? ' · DRY RUN' : ''}`;

  const pill = $('#status');
  pill.className = `status-pill ${job.status}`;
  let elapsed = '';
  if (job.started_at) elapsed = ' · ' + fmtDuration((job.finished_at ? new Date(job.finished_at) : new Date()) - new Date(job.started_at));
  const queued = job.status === 'queued' && job.queue_position > 1 ? ` · #${job.queue_position} in line` : '';
  pill.innerHTML = `${['running', 'queued'].includes(job.status) ? '<span class="dot"></span>' : ''}${STATUS_TEXT[job.status] || job.status}${queued}${elapsed}`;

  const err = $('#job-error');
  err.hidden = !job.error;
  err.innerHTML = job.error ? `<li>${esc(job.error)}</li>` : '';

  // phases
  $('#phases').innerHTML = ['1', '2', '3'].map((p) => {
    const ph = job.phases[p];
    const key = `p${p}`;
    const relevant = job.users.filter((u) => u[key] !== 'n/a');
    const doneN = relevant.filter((u) => ['created', 'updated', 'ok', 'failed', 'skipped'].includes(u[key])).length;
    let pct = relevant.length ? Math.round(100 * doneN / relevant.length) : 0;
    if (ph.status === 'done') pct = 100;
    const counter = relevant.length && ph.status !== 'skipped' ? `${doneN} / ${relevant.length}` : (ph.status === 'skipped' ? 'Not needed for this request' : '');
    return `<div class="phase ${ph.status === 'skipped' ? 'skipped' : ''}">
      <div class="top"><span class="k">PHASE ${p}</span><span class="tag ${esc(ph.status)}">${esc(CELL_TEXT[ph.status] || STATUS_TEXT[ph.status] || ph.status)}</span></div>
      <span class="t">${esc(ph.title)}</span>
      <span class="label" style="line-height:1.45">${esc(PHASE_DESC[p])}</span>
      <div class="bar"><div style="width:${pct}%"></div></div>
      <span class="sub">${esc(counter)}</span>
    </div>`;
  }).join('');

  // users table
  $('#users').innerHTML = job.users.map((u) => `<tr>
    <td><div>${esc(u.name)}</div><div class="email">${esc(u.email)} · ${esc(u.product)}</div>${u.errors.length ? `<div class="errs">${u.errors.map(esc).join('<br>')}</div>` : ''}</td>
    <td>${cellFor(u, 1)}</td><td>${cellFor(u, 2)}</td><td>${cellFor(u, 3)}</td></tr>`).join('');

  // log
  const log = $('#log');
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  if (log.textContent !== job.log) {
    log.textContent = job.log || (job.status === 'queued' ? 'Waiting for the previous request to finish…' : '');
    if (atBottom) log.scrollTop = log.scrollHeight;
  }

  $('#next-card').hidden = finished;
  $('#result').hidden = !finished;
  if (finished) renderResult();
}

function renderResult() {
  const emails = job.emails || [];
  const sel = $('#email-select');
  if (sel.options.length !== emails.length) {
    sel.innerHTML = emails.map((e, i) => `<option value="${i}">${i + 1}. ${esc(e.name)} — ${esc(e.email)}${e.failed ? ' (failed)' : ''}</option>`).join('');
  }
  showEmail(Math.min(emailIdx, Math.max(0, emails.length - 1)));

  // keys
  const res = job.result || {};
  const keys = [];
  const byKey = {};
  for (const [email, key] of Object.entries(res.packs_keys || {})) (byKey[key] = byKey[key] || []).push(email);
  for (const [key, emailsFor] of Object.entries(byKey)) keys.push({ label: 'Pack key (packs_api)', value: key, meta: emailsFor.join(', ') });
  for (const [email, key] of Object.entries(res.slapi_tokens || {})) keys.push({ label: 'SL API token', value: key, meta: email });
  $('#keys-card').hidden = !keys.length;
  $('#keys').innerHTML = keys.map((k, i) => `<div style="display:flex;flex-direction:column;gap:8px">
      <span class="sub">${esc(k.label)}</span>
      <div class="key-row"><code title="${esc(k.value)}">${esc(k.value)}</code>
        <button type="button" class="icon-btn" data-copy-key="${i}" aria-label="Copy key"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg></button></div>
      <span class="sub">${esc(k.meta)}</span></div>`).join('');
  $$('[data-copy-key]').forEach((b) => { b.onclick = async () => { await copyText(keys[Number(b.dataset.copyKey)].value); toast('Key copied'); }; });

  // todos
  const todos = job.todos || [];
  $('#todos-card').hidden = !todos.length;
  const left = todos.filter((t) => !t.done).length;
  $('#todo-count').textContent = left ? `${left} left` : 'all done';
  $('#todos').innerHTML = todos.map((t, i) => `<label class="check-row"><input type="checkbox" data-todo="${i}" ${t.done ? 'checked' : ''}><span style="color:${t.done ? 'var(--muted)' : 'var(--text)'}">${esc(t.text)}</span></label>`).join('');
  $$('[data-todo]').forEach((cb) => {
    cb.onchange = async () => {
      const i = Number(cb.dataset.todo);
      job.todos[i].done = cb.checked;
      await api(`/api/jobs/${JOB_ID}/todos/${i}`, { method: 'POST', body: { done: cb.checked } });
      renderResult();
    };
  });

  // details
  const first = (job.rows || [])[0] || {};
  const products = [...new Set((job.rows || []).map((r) => r.product))].join(' + ');
  const details = [
    ['Product', `${products} · ${(first.license_type || '').replace(/^./, (c) => c.toUpperCase())}`],
    ['Period', first.start_date ? `${first.start_date} → ${first.end_date}` : ''],
    ['Company', first['Company name'] || '—'],
    ['Packs', first.packs || 'No packs'],
    ['Modes', first.modes && first.modes !== 'none' ? first.modes : ''],
    ['API credits', first.points || ''],
    ['Deal ID', first.company_id || ''],
    ['Run', job.dry_run ? 'Dry run' : ''],
  ].filter(([, v]) => v);
  $('#details').innerHTML = details.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
  $('#again').hidden = !job.request;
  $('#again').href = `/?from=${job.id}`;
}

function showEmail(i) {
  const emails = job.emails || [];
  if (!emails.length) { $('#email-pos').textContent = 'No emails'; return; }
  emailIdx = (i + emails.length) % emails.length;
  const e = emails[emailIdx];
  $('#email-select').value = String(emailIdx);
  $('#email-pos').textContent = `Email ${emailIdx + 1} of ${emails.length}`;
  $('#email-to').textContent = e.email;
  $('#email-failed').hidden = !e.failed;
  if (renderedEmail === emailIdx) return;
  renderedEmail = emailIdx;
  const frame = $('#email-frame');
  frame.srcdoc = `<!doctype html><html><head><meta charset="utf-8"><style>${FRAME_CSS}</style></head><body>${e.html}</body></html>`;
  frame.onload = () => { frame.style.height = frame.contentDocument.documentElement.scrollHeight + 'px'; };
}

async function poll() {
  const { ok, data, status } = await api(`/api/jobs/${encodeURIComponent(JOB_ID)}`);
  if (!ok) {
    $('#title').textContent = status === 404 ? 'Request not found' : 'Could not load the request';
    return;
  }
  job = data;
  render();
  if (['queued', 'running'].includes(job.status)) setTimeout(poll, 1500);
}

$('#email-select').onchange = (e) => showEmail(Number(e.target.value));
$('#email-prev').onclick = () => showEmail(emailIdx - 1);
$('#email-next').onclick = () => showEmail(emailIdx + 1);
$('#copy-subject').onclick = async () => { await copyText(job.emails[emailIdx].subject); toast('Subject copied'); };
$('#copy-email').onclick = async () => {
  const e = job.emails[emailIdx];
  const doc = $('#email-frame').contentDocument;
  await copyRich(doc, e.html, doc.body.innerText);
  toast('Email copied — paste it into your mail client');
};
$('#log-toggle').onclick = () => {
  logOpen = !logOpen;
  $('#log').hidden = !logOpen;
  $('#log-toggle').textContent = logOpen ? 'Hide' : 'Show';
};

showUser();
poll();
