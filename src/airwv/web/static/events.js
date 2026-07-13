// Events page — list curated events; for "captured" ones, overlay the involved
// sensors' PM2.5 across the event window (before/during/after) via /api/series.
const $ = id => document.getElementById(id);
const esc = s => (s || '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const KIND = {fire:'🔥', wildfire:'🔥', explosion:'💥', haze:'🌫️', smoke:'🌫️',
              spill:'🛢️', odor:'👃', other:'📌'};
const MEDIUM = {air:'💨 air', water:'💧 water', soil:'🌱 soil', other:'📍'};
const COLORS = ['#4a7fb0', '#c9992f', '#7a6fb0', '#c0392b', '#2a7a2a', '#e07b39', '#5a6472'];
let EVENTS = [], NAMES = {};

const fmtD = iso => iso ? new Date(iso).toLocaleDateString('en-US',
  {year:'numeric', month:'short', day:'numeric'}) : '';
function dateRange(e) {
  if (!e.start_ts) return '';
  const a = fmtD(e.start_ts), b = e.end_ts ? fmtD(e.end_ts) : '';
  return b && b !== a ? `${a} – ${b}` : a;
}

function card(e) {
  const emoji = KIND[e.kind] || '📌';
  const badge = e.captured
    ? '<span class="evbadge cap">📈 sensor data</span>'
    : '<span class="evbadge">documented</span>';
  const med = e.medium && e.medium !== 'air'
    ? `<span class="evbadge med">${MEDIUM[e.medium] || e.medium}</span>` : '';
  return `<button class="evcard" data-id="${e.id}">
    <span class="evico">${emoji}</span>
    <div class="evcard-body">
      <div class="evcard-title">${esc(e.title)} ${med} ${badge}</div>
      <div class="evcard-meta">${esc(e.region || '')}${e.region ? ' · ' : ''}${dateRange(e)}</div>
      ${e.description ? `<div class="evcard-desc">${esc(e.description.slice(0, 140))}${e.description.length > 140 ? '…' : ''}</div>` : ''}
    </div></button>`;
}

async function sensorNames() {
  try {
    const d = await (await fetch('/api/sensors')).json();
    d.forEach(s => { NAMES[s.sensor_id] = s.name; });
  } catch (e) { /* names optional */ }
}

async function drawChart(e) {
  const pad = 48 * 3600 * 1000;  // ±2 days around the window
  const t0 = e.start_ts ? new Date(e.start_ts).getTime() - pad : null;
  const t1 = (e.end_ts ? new Date(e.end_ts) : new Date(e.start_ts)).getTime() + pad;
  // /api/series defaults to a sensor's LAST 90 days, so historical events need explicit
  // date bounds or they come back empty. Pass the padded window as start/end (YYYY-MM-DD).
  const ymd = ms => new Date(ms).toISOString().slice(0, 10);
  const q = (t0 && t1) ? `&start=${ymd(t0)}&end=${ymd(t1)}` : '';
  const traces = [];
  for (let i = 0; i < e.sensor_ids.length; i++) {
    const sid = e.sensor_ids[i];
    try {
      const d = await (await fetch(`/api/series/${sid}?field=pm2_5${q}`)).json();
      const pts = (d.points || []).filter(p => {
        const t = new Date(p.ts).getTime();
        return (!t0 || t >= t0) && t <= t1;
      });
      if (!pts.length) continue;
      // EPA reference-monitor ids (NN-NNN-NNNN) aren't in /api/sensors — label them
      const label = NAMES[sid] || (/^\d+-\d+-\d+$/.test(sid) ? `EPA monitor ${sid}` : sid);
      traces.push({
        x: pts.map(p => p.ts), y: pts.map(p => p.value), name: label,
        mode: 'lines', line: {color: COLORS[i % COLORS.length], width: 1.6},
      });
    } catch (err) { /* skip */ }
  }
  if (!traces.length) { $('ev-chartnote').textContent = 'No sensor series available for this window.'; return; }
  const shapes = e.start_ts ? [{
    type: 'rect', xref: 'x', yref: 'paper', x0: e.start_ts, x1: e.end_ts || e.start_ts,
    y0: 0, y1: 1, fillcolor: '#ff000010', line: {width: 0}, layer: 'below',
  }] : [];
  Plotly.newPlot('ev-chart', traces, {
    margin: {t: 10, r: 10, b: 40, l: 44}, height: 320,
    yaxis: {title: 'PM2.5 µg/m³'}, legend: {orientation: 'h'},
    shapes, annotations: e.start_ts ? [{
      x: e.start_ts, y: 1, yref: 'paper', text: 'event', showarrow: false,
      font: {size: 11, color: '#a00'}, xanchor: 'left',
    }] : [],
  }, {displayModeBar: false, responsive: true});
  $('ev-chartnote').textContent = 'Shaded band = the event window. PM2.5 (hourly median for '
    + 'community sensors — provisional/uncorrected; daily mean for EPA reference monitors).';
}

function factRow(label, val) {
  return val ? `<div class="ev-fact"><b>${label}</b><span>${esc(val)}</span></div>` : '';
}
function openEvent(e) {
  $('ev-title').textContent = `${KIND[e.kind] || '📌'} ${e.title}`;
  $('ev-metaline').innerHTML = [e.region, dateRange(e), e.scope,
    e.captured ? '📈 sensor data' : 'documented'].filter(Boolean).map(esc).join(' · ');
  // facts: origin / scope / regions affected
  const facts = factRow('Likely origin', e.origin) + factRow('Scope', e.scope)
    + factRow('Regions affected', e.regions_affected);
  // links to related facilities (Sources page) and community reports
  const refs = (e.source_refs || []).map(n =>
    `<a href="/sources#facility=${encodeURIComponent(n)}">🏭 ${esc(n)}</a>`).join(' · ');
  const reps = (e.report_ids || []).map(id =>
    `<a href="/air">📣 report #${id}</a>`).join(' · ');
  const links = (refs || reps)
    ? `<div class="ev-links">${refs}${refs && reps ? ' · ' : ''}${reps}</div>` : '';
  $('ev-desc').innerHTML = (facts ? `<div class="ev-facts">${facts}</div>` : '')
    + `<div class="ev-body">${esc(e.description || '')}</div>` + links;
  const srcs = (e.sources || []).filter(s => s && (s.url || s.label));
  $('ev-sources').innerHTML = srcs.length
    ? '<b class="ev-sub">Sources</b><ul>' + srcs.map(s =>
        `<li>${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.label || s.url)}</a>` : esc(s.label)}</li>`).join('') + '</ul>'
    : '';
  const cap = e.captured && (e.sensor_ids || []).length;
  $('ev-chartwrap').style.display = cap ? 'block' : 'none';
  $('ev-nodata').style.display = cap ? 'none' : 'block';
  $('ev-detail').classList.add('on');
  if (cap) { $('ev-chart').innerHTML = ''; drawChart(e); }
}

// ---- NRC reported spills (map layer + list on the Events page) ----
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
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 18, attribution: '© OpenStreetMap'}).addTo(spillMap);
  }
  if (spillLayer) spillLayer.remove();
  spillLayer = L.markerClusterGroup ? L.markerClusterGroup({chunkedLoading: true, maxClusterRadius: 40}) : L.layerGroup();
  list.forEach(s => {
    if (s.lat == null || s.lon == null) return;
    const col = s.reached_water ? '#2c7fb8' : '#8a94a0', exact = s.geo === 'exact';
    L.circleMarker([s.lat, s.lon], {radius: 6, color: col, weight: exact ? 1 : 2,
      fillColor: col, fillOpacity: exact ? 0.85 : 0.15, dashArray: exact ? null : '3'})
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

async function init() {
  await sensorNames();
  loadSpills();
  try {
    const d = await (await fetch('/api/events')).json();
    EVENTS = d.results || [];
  } catch (e) { $('ev-list').innerHTML = '<p class="meta" style="padding:14px">Could not load events.</p>'; return; }
  $('ev-list').innerHTML = EVENTS.length
    ? EVENTS.map(card).join('')
    : '<p class="meta" style="padding:14px">No events yet.</p>';
  document.body.addEventListener('click', ev => {
    const b = ev.target.closest('.evcard');
    if (b) openEvent(EVENTS.find(x => x.id === +b.dataset.id));
  });
  $('ev-close').addEventListener('click', () => $('ev-detail').classList.remove('on'));
  $('ev-detail').addEventListener('click', ev => { if (ev.target === $('ev-detail')) $('ev-detail').classList.remove('on'); });
}
init();
