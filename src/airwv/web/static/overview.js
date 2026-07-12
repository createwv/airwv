// Public Overview landing. One /api/sensors fetch drives the at-a-glance stats and
// the simplified map; /api/reports drives the recent-activity feed. Read-only, no controls.
const $ = id => document.getElementById(id);
const esc = s => (s || '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

// EPA PM2.5 AQI categories (mirror of PM25_BANDS in app.py) — for the headline label.
const PM_BANDS = [
  {max: 12.0, label: 'Good', color: '#00e400'},
  {max: 35.4, label: 'Moderate', color: '#ffff00'},
  {max: 55.4, label: 'Unhealthy for sensitive groups', color: '#ff7e00'},
  {max: 150.4, label: 'Unhealthy', color: '#ff0000'},
  {max: 250.4, label: 'Very unhealthy', color: '#8f3f97'},
  {max: Infinity, label: 'Hazardous', color: '#7e0023'},
];
const band = v => PM_BANDS.find(b => v <= b.max) || PM_BANDS[PM_BANDS.length - 1];
const median = a => { const s = [...a].sort((x, y) => x - y); const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const fmtDate = iso => iso ? new Date(iso).toLocaleString('en-US',
  {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}) : '—';

const DOMAIN_EMOJI = {air:'💨', water:'💧', soil:'🌱', wildlife:'🦌', violation:'⚠️', other:'📣'};

function statCard(value, label, sub, color){
  return `<div class="statcard">
    <div class="statval" ${color ? `style="color:${color}"` : ''}>${value}</div>
    <div class="statlabel">${label}</div>${sub ? `<div class="statsub">${sub}</div>` : ''}</div>`;
}

function renderStats(sensors){
  const community = sensors.filter(s => s.kind === 'community');
  const refs = sensors.filter(s => s.kind === 'reference');
  const withVal = community.filter(s => s.latest_pm2_5 != null);
  const vals = withVal.map(s => s.latest_pm2_5);
  const fresh = sensors.map(s => s.last_ts).filter(Boolean).sort().pop();

  if (!vals.length){
    $('ov-headline').innerHTML = '📡 No current community readings — see the '
      + '<a href="/air">full dashboard</a> for history.';
    $('ov-stats').innerHTML = statCard(community.length, 'community sensors')
      + statCard(refs.length, 'EPA monitors');
    return;
  }
  const typ = median(vals), hi = Math.max(...vals);
  const hiS = withVal.find(s => s.latest_pm2_5 === hi);
  const tb = band(typ), hb = band(hi);
  const elevated = withVal.filter(s => s.latest_pm2_5 > 35.4);

  $('ov-stats').innerHTML =
    statCard(typ.toFixed(1), 'typical PM2.5 now', tb.label + ' · µg/m³', tb.color === '#ffff00' ? '#b59a00' : tb.color) +
    statCard(withVal.length, 'sensors reporting', `of ${community.length} community`) +
    statCard(hi.toFixed(1), 'highest now', esc(hiS ? hiS.name : ''), hb.color === '#ffff00' ? '#b59a00' : hb.color) +
    statCard(refs.length, 'EPA reference monitors', 'for validation');

  const h = $('ov-headline');
  if (elevated.length){
    const worst = elevated.sort((a, b) => b.latest_pm2_5 - a.latest_pm2_5)[0];
    h.className = 'ov-headline warn';
    h.innerHTML = `⚠️ <b>Elevated PM2.5</b> at ${esc(worst.name)} `
      + `(${worst.latest_pm2_5.toFixed(0)} µg/m³ — ${band(worst.latest_pm2_5).label})`
      + (elevated.length > 1 ? ` and ${elevated.length - 1} other site${elevated.length > 2 ? 's' : ''}.` : '.');
  } else {
    h.className = 'ov-headline good';
    h.innerHTML = `✅ Air looks <b>good</b> across the network right now `
      + `(typical PM2.5 ${typ.toFixed(1)} µg/m³).`;
  }
  h.innerHTML += ` <span class="meta">As of ${fmtDate(fresh)}.</span>`;
}

function drawMap(sensors){
  const pts = sensors.filter(s => s.lat != null && s.lon != null);
  const map = L.map('ov-map', {scrollWheelZoom: false}).setView([38.9, -80.5], 7);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    {maxZoom: 18, attribution: '© OpenStreetMap'}).addTo(map);
  const group = [];
  pts.forEach(s => {
    const ref = s.kind === 'reference';
    const val = s.latest_pm2_5 != null ? `${s.latest_pm2_5.toFixed(1)} µg/m³ PM2.5` : 'no current reading';
    const m = L.circleMarker([s.lat, s.lon], {
      radius: ref ? 6 : 8, color: ref ? '#333' : '#111', weight: ref ? 1 : 2,
      fillColor: s.color, fillOpacity: ref ? 0.7 : 0.85})
      .bindPopup(`<b>${esc(s.name)}</b><br>${val}${ref ? '<br><i>EPA reference monitor</i>' : ''}`);
    m.addTo(map); group.push([s.lat, s.lon]);
  });
  if (group.length) map.fitBounds(group, {padding: [30, 30], maxZoom: 9});
  $('ov-legend').innerHTML = 'Dots = community sensors · rings = EPA monitors · '
    + 'color = latest PM2.5 (green good → red unhealthy). Click a dot for details.';
  window.AIRWV_MAP = map;               // reporting.js drops the report pin here
  window.AIRWV_ON_REPORT = loadReports; // refresh the feed after a submission
}

async function loadReports(){
  try {
    const d = await (await fetch('/api/reports')).json();
    const rows = (d.results || []).slice(0, 6);
    if (!rows.length){ $('ov-reports').innerHTML =
      '<p class="meta" style="padding:0 14px 12px">No community reports yet — be the first to '
      + '<a href="/air#report">report a concern</a>.</p>'; return; }
    $('ov-reports').innerHTML = rows.map(r => {
      const emoji = DOMAIN_EMOJI[r.domain] || '📣';
      const badge = r.verified ? '<span class="rbadge ok">verified</span>'
                               : '<span class="rbadge">unverified</span>';
      return `<div class="ov-rep"><span class="rico">${emoji}</span>
        <div><b>${esc(r.domain)}${r.category ? ' — ' + esc(r.category) : ''}</b> ${badge}
        <div class="meta">${esc(r.area_label || '')}${r.area_label ? ' · ' : ''}${fmtDate(r.created_at)}</div>
        ${r.description ? `<div class="ov-repdesc">${esc(r.description)}</div>` : ''}</div></div>`;
    }).join('');
  } catch(e){ $('ov-reports').innerHTML =
    '<p class="meta" style="padding:0 14px 12px">Could not load reports.</p>'; }
}

async function init(){
  try {
    const sensors = await (await fetch('/api/sensors')).json();
    renderStats(sensors);
    drawMap(sensors);
  } catch(e){
    $('ov-headline').innerHTML = 'Could not load current conditions. '
      + '<a href="/air">Open the full dashboard →</a>';
  }
  loadReports();
}
init();
