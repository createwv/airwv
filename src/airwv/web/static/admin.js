// AirWV admin moderation console. Token-gated: the token is kept in localStorage and
// sent as X-Admin-Token on every call. Actions map to POST /api/admin/reports/{id}.
const $ = id => document.getElementById(id);
let TOKEN = localStorage.getItem('airwv_admin_token') || '';

function setToken(){
  const t = prompt('Admin token (set AIRWV_ADMIN_TOKEN on the server):', TOKEN);
  if (t !== null){ TOKEN = t.trim(); localStorage.setItem('airwv_admin_token', TOKEN); loadQueue(); }
}
async function adminFetch(url, opts = {}){
  opts.headers = Object.assign({'X-Admin-Token': TOKEN, 'Content-Type': 'application/json'}, opts.headers || {});
  const r = await fetch(url, opts);
  if (r.status === 401) throw new Error('unauthorized');
  return r;
}
const esc = s => (s || '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

async function act(id, action){
  await adminFetch(`/api/admin/reports/${id}`, {method:'POST',
    body: JSON.stringify({action, verified_by: 'maintainer'})});
  loadQueue();
}
async function fbAct(id, status){
  await adminFetch(`/api/admin/feedback/${id}`, {method:'POST', body: JSON.stringify({status})});
  loadQueue();
}

function reportCard(r){
  const priv = [r.suspected_org ? `org: <b>${esc(r.suspected_org)}</b>${r.org_public?' (public)':' (private)'}` : '',
                r.contact_email ? `email: ${esc(r.contact_email)}` : '',
                r.contact_phone ? `phone: ${esc(r.contact_phone)}` : ''].filter(Boolean).join(' · ');
  const when = (r.created_at || '').slice(0, 16).replace('T', ' ');
  return `<div class="acard">
    <div><b>#${r.id} ${esc(r.domain)} — ${esc(r.category) || '(no category)'}</b>
      <span class="tag">${esc(r.stage)}</span>
      ${r.flags_count ? `<span class="tag flag">⚑${r.flags_count}</span>` : ''}
      ${r.screen_reason ? `<span class="meta"> held: ${esc(r.screen_reason)}</span>` : ''}</div>
    <div>${esc(r.description) || '<i>no description</i>'}</div>
    <div class="meta">${when} · ${(r.lat||0).toFixed(4)}, ${(r.lon||0).toFixed(4)}${priv ? ' · ' + priv : ''}${r.mod_note ? ' · note: ' + esc(r.mod_note) : ''}</div>
    <div class="arow">
      ${r.stage !== 'confirmed' ? `<button onclick="act(${r.id},'confirm')">✓ Verify</button>` : ''}
      ${r.stage === 'held' ? `<button onclick="act(${r.id},'publish')">Publish (unverified)</button>` : ''}
      ${r.suspected_org && !r.org_public ? `<button onclick="act(${r.id},'approve_org')">Show org publicly</button>` : ''}
      <button class="danger" onclick="act(${r.id},'remove')">Remove</button>
    </div></div>`;
}
function fbCard(f){
  const when = (f.created_at || '').slice(0, 16).replace('T', ' ');
  return `<div class="acard">
    <div><b>#${f.id} ${esc(f.kind)}</b> <span class="tag">${esc(f.status)}</span></div>
    <div>${esc(f.message)}</div>
    <div class="meta">${when}${f.contact ? ' · ' + esc(f.contact) : ''}${f.page ? ' · ' + esc(f.page) : ''}</div>
    <div class="arow"><button onclick="fbAct(${f.id},'triaged')">Triaged</button>
      <button onclick="fbAct(${f.id},'done')">Done</button></div></div>`;
}

async function loadQueue(){
  if (!TOKEN){ $('admin-list').innerHTML = '<p class="meta">Enter the admin token to load the queues.</p>'; return; }
  const q = $('admin-queue').value;
  try {
    if (q === 'feedback'){
      const d = await (await adminFetch('/api/admin/feedback')).json();
      $('admin-count').textContent = `${d.results.length} feedback item(s)`;
      $('admin-list').innerHTML = d.results.map(fbCard).join('') || '<p class="meta">none</p>';
    } else {
      const d = await (await adminFetch(`/api/admin/reports?status=${q}`)).json();
      $('admin-count').textContent = `${d.results.length} report(s)`;
      $('admin-list').innerHTML = d.results.map(reportCard).join('') || '<p class="meta">none</p>';
    }
    $('admin-auth').textContent = '🔓 authenticated';
  } catch(e){
    $('admin-auth').textContent = '🔒 token required or invalid';
    $('admin-list').innerHTML = '<p class="meta">Not authorized — click "Enter / change token".</p>';
  }
}
$('admin-login').addEventListener('click', setToken);
$('admin-refresh').addEventListener('click', loadQueue);
$('admin-queue').addEventListener('change', loadQueue);
loadQueue();
