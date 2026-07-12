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

function eventCard(e){
  const when = [e.start_ts, e.end_ts].filter(Boolean).map(s => s.slice(0,10)).join(' → ');
  return `<div class="acard">
    <div><b>#${e.id} ${esc(e.title)}</b> <span class="tag">${esc(e.status)}</span>
      <span class="tag">${esc(e.kind)}</span>
      ${e.captured ? '<span class="tag cap">📈 sensor data</span>' : ''}</div>
    <div class="meta">${esc(e.region||'')}${e.region?' · ':''}${when} · ${(e.sensor_ids||[]).length} sensor(s) · ${(e.sources||[]).length} source(s)</div>
    <div class="arow">
      ${e.status !== 'published' ? `<button onclick="evSet(${e.id},'published')">Publish</button>` : `<button onclick="evSet(${e.id},'draft')">Unpublish</button>`}
      <button class="danger" onclick="evDelete(${e.id})">Delete</button></div></div>`;
}
async function evSet(id, status){
  const e = ADMIN_EVENTS.find(x => x.id === id); if (!e) return;
  await adminFetch(`/api/admin/events/${id}`, {method:'POST', body: JSON.stringify({...e, status})});
  loadQueue();
}
async function evDelete(id){
  if (!confirm('Delete this event?')) return;
  await adminFetch(`/api/admin/events/${id}?action=delete`, {method:'POST', body: JSON.stringify({title:'x'})});
  loadQueue();
}
async function evCreate(){
  const g = id => $(id).value.trim();
  const csv = id => g(id).split(',').map(s => s.trim()).filter(Boolean);
  const body = {
    title: g('ev-f-title'), kind: g('ev-f-kind'), region: g('ev-f-region') || null,
    start_ts: g('ev-f-start') || null, end_ts: g('ev-f-end') || null,
    description: g('ev-f-desc') || null, origin: g('ev-f-origin') || null,
    scope: g('ev-f-scope') || null, regions_affected: g('ev-f-regions') || null,
    captured: $('ev-f-captured').checked, sensor_ids: csv('ev-f-sensors'),
    source_refs: csv('ev-f-srcrefs'), report_ids: csv('ev-f-reports').map(Number).filter(Boolean),
    sources: g('ev-f-srcurl') ? [{label: g('ev-f-srclabel') || g('ev-f-srcurl'), url: g('ev-f-srcurl')}] : [],
    status: 'published',
  };
  if (!body.title){ alert('Title required'); return; }
  await adminFetch('/api/admin/events', {method:'POST', body: JSON.stringify(body)});
  loadQueue();
}
const EV_FORM = `<div class="acard evform">
  <b>➕ New event</b>
  <input id="ev-f-title" placeholder="Title *">
  <div class="evrow">
    <select id="ev-f-kind"><option value="fire">fire</option><option value="wildfire">wildfire</option>
      <option value="explosion">explosion</option><option value="haze">haze</option>
      <option value="spill">spill</option><option value="odor">odor</option><option value="other">other</option></select>
    <input id="ev-f-region" placeholder="Region / place">
  </div>
  <div class="evrow"><label class="evlbl">Start <input id="ev-f-start" type="date"></label>
    <label class="evlbl">End <input id="ev-f-end" type="date"></label></div>
  <textarea id="ev-f-desc" rows="3" placeholder="Description"></textarea>
  <input id="ev-f-origin" placeholder="Likely / suspected origin (cause)">
  <div class="evrow">
    <select id="ev-f-scope"><option value="">Scope…</option><option>Local</option>
      <option>Regional</option><option>Multi-state</option><option>Continental</option></select>
    <input id="ev-f-regions" placeholder="Regions affected (free text)"></div>
  <label class="evchk"><input type="checkbox" id="ev-f-captured"> Our sensors captured this</label>
  <input id="ev-f-sensors" placeholder="Sensor ids, comma-separated (e.g. 214373,214357)">
  <input id="ev-f-srcrefs" placeholder="Related facility names (comma-sep, must match Sources)">
  <input id="ev-f-reports" placeholder="Related report ids (comma-sep)">
  <div class="evrow"><input id="ev-f-srclabel" placeholder="Citation label">
    <input id="ev-f-srcurl" placeholder="Citation URL"></div>
  <button class="primary" onclick="evCreate()">Create event</button></div>`;

let ADMIN_EVENTS = [];
async function loadQueue(){
  if (!TOKEN){ $('admin-list').innerHTML = '<p class="meta">Enter the admin token to load the queues.</p>'; return; }
  const q = $('admin-queue').value;
  try {
    if (q === 'events'){
      const d = await (await adminFetch('/api/admin/events')).json();
      ADMIN_EVENTS = d.results;
      $('admin-count').textContent = `${d.results.length} event(s)`;
      $('admin-list').innerHTML = EV_FORM + d.results.map(eventCard).join('');
    } else if (q === 'feedback'){
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
async function notifyTest(){
  try {
    const d = await (await adminFetch('/api/admin/notify-test', {method:'POST'})).json();
    alert(d.sent ? `Sent (slack:${d.slack} discord:${d.discord}) — check your channel.` : d.detail);
  } catch(e){ alert('Not authorized — enter the admin token first.'); }
}
$('admin-login').addEventListener('click', setToken);
$('admin-refresh').addEventListener('click', loadQueue);
$('admin-notify-test').addEventListener('click', notifyTest);
$('admin-queue').addEventListener('change', loadQueue);
loadQueue();
