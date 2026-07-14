// Spills page — the NRC reported-spills feed: a map + filterable list of oil/chemical
// releases reported to the federal National Response Center. Split off from Events
// because these are initial, unverified reports — a distinct kind of record.
const $ = id => document.getElementById(id);
const esc = s => (s || '').replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
const fmtD = iso => iso ? new Date(iso).toLocaleDateString('en-US',
  { year: 'numeric', month: 'short', day: 'numeric' }) : '';

let SPILLS = [], spillMap, spillLayer, SPILL_META = {};
const spillMats = s => (s.materials || []).map(m => m.name).filter(Boolean);

function spillCard(s) {
  const water = s.reached_water ? '<span class="evbadge med">💧 reached water</span>' : '';
  const loc = [s.city, s.county && s.county + ' Co.'].filter(Boolean).join(', ');
  return `<div class="evcard" style="cursor:default">
    <span class="evico">🛢️</span>
    <div class="evcard-body">
      <div class="evcard-title">${esc(spillMats(s).slice(0, 3).join(', ') || 'Reported release')} ${water}</div>
      <div class="evcard-meta">${esc(loc)}${loc ? ' · ' : ''}${fmtD(s.date)}${s.body_of_water ? ' · into ' + esc(s.body_of_water) : ''}</div>
      ${s.description ? `<div class="evcard-desc">${esc(s.description.slice(0, 150))}${s.description.length > 150 ? '…' : ''}</div>` : ''}
    </div></div>`;
}
function spillPopup(s) {
  const mats = (s.materials || []).map(m => `${esc(m.name)}${m.amount ? ` (${m.amount} ${esc(m.unit || '')})` : ''}${m.reached_water ? ' 💧' : ''}`).join('<br>');
  const loc = [s.city, s.county && s.county + ' Co.'].filter(Boolean).join(', ');
  return `<b>${esc(spillMats(s)[0] || 'Reported release')}</b><br><small>${esc(loc)} · ${fmtD(s.date)}</small>`
    + (s.type ? `<br><small>${esc(s.type)}${s.company ? ' · ' + esc(s.company) : ''}</small>` : '')
    + (mats ? `<br><small>Material:<br>${mats}</small>` : '')
    + (s.body_of_water ? `<br><small>💧 into ${esc(s.body_of_water)}</small>` : '')
    + (s.description ? `<br><small>${esc(s.description.slice(0, 220))}</small>` : '')
    + `<br><small class="meta">NRC report #${esc('' + s.report_id)}${s.geo === 'county' ? ' · approx (county)' : ''}</small>`;
}
function drawSpills(list) {
  if (!spillMap) {
    spillMap = L.map('spill-map').setView([38.9, -80.5], 7);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 18, attribution: '© OpenStreetMap' }).addTo(spillMap);
  }
  if (spillLayer) spillLayer.remove();
  spillLayer = L.markerClusterGroup ? L.markerClusterGroup({ chunkedLoading: true, maxClusterRadius: 40 }) : L.layerGroup();
  list.forEach(s => {
    if (s.lat == null || s.lon == null) return;
    const col = s.reached_water ? '#2c7fb8' : '#8a94a0', exact = s.geo === 'exact';
    L.circleMarker([s.lat, s.lon], { radius: 6, color: col, weight: exact ? 1 : 2,
      fillColor: col, fillOpacity: exact ? 0.85 : 0.15, dashArray: exact ? null : '3' })
      .bindPopup(spillPopup(s)).addTo(spillLayer);
  });
  spillLayer.addTo(spillMap);
}
function renderSpills() {
  const water = $('spill-water').checked, yr = $('spill-year').value;
  const list = SPILLS.filter(s => (!water || s.reached_water) && (!yr || (s.date || '').startsWith(yr)));
  const s = SPILL_META.summary || {};
  $('spill-count').textContent = `${list.length} reports · ${s.reached_water || 0} of ${s.total || 0} reached water`;
  drawSpills(list);
  $('spill-list').innerHTML = list.slice(0, 40).map(spillCard).join('')
    + (list.length > 40 ? `<p class="meta" style="padding:8px 14px">Showing 40 of ${list.length} — filter by year, water, or use the map.</p>` : '');
}
async function loadSpills() {
  try { const d = await (await fetch('/api/nrc-spills')).json(); SPILLS = d.spills || []; SPILL_META = d; }
  catch (e) { $('spill-list').innerHTML = '<p class="meta" style="padding:14px">Could not load reported spills.</p>'; return; }
  $('spill-src').textContent = SPILL_META.fetched_at ? `· NRC, as of ${SPILL_META.fetched_at}` : '';
  $('spill-disc').textContent = SPILL_META.disclaimer || '';
  const years = [...new Set(SPILLS.map(x => (x.date || '').slice(0, 4)).filter(Boolean))].sort().reverse();
  $('spill-year').innerHTML = '<option value="">All years</option>' + years.map(y => `<option>${y}</option>`).join('');
  $('spill-water').addEventListener('change', renderSpills);
  $('spill-year').addEventListener('change', renderSpills);
  renderSpills();
  setTimeout(() => spillMap && spillMap.invalidateSize(), 200);
}
loadSpills();
