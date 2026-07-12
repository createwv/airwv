// Field-reading entry (trained scientists). Token-gated submission (shares the admin
// token), geolocation / map-pick for location, optional downscaled meter photo. Recent
// readings render on a map + list for everyone.
const $ = id => document.getElementById(id);
const esc = s => (s || '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const MED = {air: '💨', water: '💧', soil: '🌱', other: '📍'};
const MEDCOLOR = {air: '#4a7fb0', water: '#2c9fd6', soil: '#8a5a3b', other: '#7a6fb0'};
let TOKEN = localStorage.getItem('airwv_admin_token') || '';
let map, layer, loc = null, tmpMarker = null, picking = false;

function setToken() {
  const t = prompt('Field token (same as the admin token):', TOKEN);
  if (t !== null) { TOKEN = t.trim(); localStorage.setItem('airwv_admin_token', TOKEN); showAuth(); }
}
function showAuth() { $('fr-auth').textContent = TOKEN ? '🔓 token set' : '🔒 enter the field token to submit'; }

// downscale a photo to keep the upload small; returns a jpeg data URL
function fileToDataURL(file, maxDim = 1280) {
  return new Promise((resolve, reject) => {
    const img = new Image(), url = URL.createObjectURL(file);
    img.onload = () => {
      const scale = Math.min(1, maxDim / Math.max(img.width, img.height));
      const c = document.createElement('canvas');
      c.width = Math.round(img.width * scale); c.height = Math.round(img.height * scale);
      c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      resolve(c.toDataURL('image/jpeg', 0.82));
    };
    img.onerror = reject; img.src = url;
  });
}

function setLoc(lat, lon, label) {
  loc = {lat, lon};
  $('fr-locinfo').textContent = `📍 ${lat.toFixed(4)}, ${lon.toFixed(4)}${label ? ' · ' + label : ''}`;
  if (tmpMarker) tmpMarker.remove();
  tmpMarker = L.marker([lat, lon]).addTo(map);
  map.setView([lat, lon], 13);
}

async function submit() {
  if (!TOKEN) { setToken(); if (!TOKEN) return; }
  if (!loc) { $('fr-result').textContent = 'Set a location first.'; return; }
  const value = parseFloat($('fr-value').value);
  if (!$('fr-param').value.trim() || isNaN(value)) { $('fr-result').textContent = 'Parameter and a numeric value are required.'; return; }
  $('fr-submit').disabled = true; $('fr-result').textContent = 'Submitting…';
  let photo = null;
  const f = $('fr-photo').files[0];
  if (f) { try { photo = await fileToDataURL(f); } catch (e) { /* skip photo */ } }
  const body = {
    submitter: $('fr-submitter').value.trim() || 'field team', medium: $('fr-medium').value,
    parameter: $('fr-param').value.trim(), value, unit: $('fr-unit').value.trim(),
    method: $('fr-method').value.trim() || null, lat: loc.lat, lon: loc.lon,
    notes: $('fr-notes').value.trim() || null, photo,
  };
  try {
    const r = await fetch('/api/field-readings', {method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Admin-Token': TOKEN}, body: JSON.stringify(body)});
    if (r.status === 401) { $('fr-result').textContent = 'Not authorized — check the field token.'; $('fr-submit').disabled = false; return; }
    if (!r.ok) throw new Error('bad status');
    $('fr-result').textContent = '✓ Saved. Thank you.';
    ['fr-value', 'fr-notes', 'fr-method'].forEach(id => $(id).value = '');
    $('fr-photo').value = '';
    loadRecent();
  } catch (e) { $('fr-result').textContent = 'Could not submit — try again.'; }
  $('fr-submit').disabled = false;
}

const fmt = iso => iso ? new Date(iso).toLocaleString('en-US', {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}) : '';

async function loadRecent() {
  let rows = [];
  try { rows = (await (await fetch('/api/field-readings')).json()).results || []; } catch (e) { return; }
  $('fr-count').textContent = `· ${rows.length}`;
  if (layer) layer.remove();
  layer = L.layerGroup();
  rows.forEach(r => {
    if (r.lat == null) return;
    L.circleMarker([r.lat, r.lon], {radius: 7, color: '#333', weight: 1, fillColor: MEDCOLOR[r.medium] || '#555', fillOpacity: 0.9})
      .bindPopup(`<b>${MED[r.medium] || ''} ${esc(r.parameter)}: ${r.value} ${esc(r.unit || '')}</b>`
        + `<br><small>${esc(r.submitter)} · ${fmt(r.observed_at || r.created_at)}</small>`
        + (r.method ? `<br><small>${esc(r.method)}</small>` : '')
        + (r.has_photo ? `<br><a href="/api/field-readings/${r.id}/photo" target="_blank">📷 meter photo</a>` : ''))
      .addTo(layer);
  });
  layer.addTo(map);
  $('fr-list').innerHTML = rows.slice(0, 12).map(r => `<div class="ov-rep">
    <span class="rico">${MED[r.medium] || '📍'}</span>
    <div><b>${esc(r.parameter)}: ${r.value} ${esc(r.unit || '')}</b>
    <div class="meta">${esc(r.submitter)} · ${fmt(r.observed_at || r.created_at)}${r.area_label ? ' · ' + esc(r.area_label) : ''}
      ${r.has_photo ? ` · <a href="/api/field-readings/${r.id}/photo" target="_blank">📷</a>` : ''}</div>
    ${r.notes ? `<div class="ov-repdesc">${esc(r.notes)}</div>` : ''}</div></div>`).join('')
    || '<p class="meta" style="padding:10px 14px">No field readings yet.</p>';
}

function init() {
  showAuth();
  map = L.map('fr-map').setView([38.9, -80.5], 7);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 18, attribution: '© OpenStreetMap'}).addTo(map);
  map.on('click', e => { if (picking) { picking = false; setLoc(e.latlng.lat, e.latlng.lng); } });
  $('fr-login').addEventListener('click', setToken);
  $('fr-submit').addEventListener('click', submit);
  $('fr-pick').addEventListener('click', () => { picking = true; $('fr-locinfo').textContent = 'tap the map…'; });
  $('fr-geo').addEventListener('click', () => {
    if (!navigator.geolocation) { $('fr-locinfo').textContent = 'geolocation not available'; return; }
    $('fr-locinfo').textContent = 'locating…';
    navigator.geolocation.getCurrentPosition(
      p => setLoc(p.coords.latitude, p.coords.longitude, 'GPS'),
      () => { $('fr-locinfo').textContent = 'could not get location — use "Set on map"'; },
      {enableHighAccuracy: true, timeout: 10000});
  });
  loadRecent();
}
init();
