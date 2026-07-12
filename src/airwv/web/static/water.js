// Water page — USGS real-time gauges on a map, colored by the selected measure;
// click a site to chart its recent history. First cut of the "Water" lens.
const $ = id => document.getElementById(id);
const esc = s => (s || '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const NEUTRAL = '#9aa0a6';

// per-parameter metadata + coloring. Some (pH) are non-monotonic, so a function not bands.
const WATER_PARAMS = {
  ph:          {label: 'pH', unit: '', note: 'Healthy range ~6.5–9. Mining/acid drainage pushes it low.',
                color: v => (v >= 6.5 && v <= 9) ? '#00e400' : (v >= 6 && v <= 9.5 ? '#ffff00' : '#ff0000')},
  do:          {label: 'Dissolved oxygen', unit: 'mg/L', note: 'Above ~5 mg/L supports aquatic life; low DO stresses fish.',
                color: v => v >= 7 ? '#00e400' : v >= 5 ? '#ffff00' : v >= 4 ? '#ff7e00' : '#ff0000'},
  conductance: {label: 'Conductivity', unit: 'µS/cm', note: 'Higher = more dissolved salts — a marker of mining/industrial influence.',
                color: v => v < 500 ? '#00e400' : v < 1000 ? '#ffff00' : v < 2000 ? '#ff7e00' : '#ff0000'},
  turbidity:   {label: 'Turbidity', unit: 'FNU', note: 'Higher = more suspended sediment (runoff, disturbance).',
                color: v => v < 10 ? '#00e400' : v < 50 ? '#ffff00' : v < 100 ? '#ff7e00' : '#ff0000'},
  temperature: {label: 'Water temperature', unit: '°C', note: 'Warmer water holds less oxygen.', color: () => '#4a7fb0'},
  discharge:   {label: 'Streamflow', unit: 'cfs', note: 'How much water is flowing past — context for everything else.', color: () => '#4a7fb0'},
  gage_height: {label: 'Gage height', unit: 'ft', note: 'River stage (height) at the gauge.', color: () => '#4a7fb0'},
};
const ORDER = ['ph', 'do', 'conductance', 'turbidity', 'temperature', 'discharge', 'gage_height'];

let SITES = [], map, layer, current = 'ph';

function colorFor(site, param) {
  const l = site.latest[param];
  if (!l) return null;
  return (WATER_PARAMS[param].color)(l.value);
}

function draw() {
  if (layer) layer.remove();
  layer = L.layerGroup();
  const meta = WATER_PARAMS[current];
  let withVal = 0;
  SITES.forEach(s => {
    if (s.lat == null || s.lon == null) return;
    const l = s.latest[current];
    const col = l ? colorFor(s, current) : NEUTRAL;
    if (l) withVal++;
    const val = l ? `${l.value}${meta.unit ? ' ' + meta.unit : ''}` : 'not measured here';
    L.circleMarker([s.lat, s.lon], {radius: l ? 7 : 4, color: '#333', weight: l ? 1 : 0.5,
      fillColor: col, fillOpacity: l ? 0.9 : 0.4})
      .bindPopup(`<b>${esc(s.name)}</b><br>${meta.label}: <b>${val}</b>`
        + `<br><small>click to chart · USGS ${esc(s.site_id)}</small>`)
      .on('click', () => showSite(s)).addTo(layer);
  });
  layer.addTo(map);
  $('w-count').textContent = `· ${withVal} of ${SITES.length} sites report ${meta.label.toLowerCase()}`;
  $('w-note').textContent = meta.note;
  $('w-legend').innerHTML = legend(current);
}

function legend(param) {
  if (['temperature', 'discharge', 'gage_height'].includes(param))
    return 'Colored dots = sites reporting this measure; faint = not measured here. Click a dot to chart it.';
  const swatches = {ph: [['#00e400', '6.5–9 (healthy)'], ['#ffff00', 'slightly off'], ['#ff0000', 'acidic/basic']],
    do: [['#00e400', '≥7'], ['#ffff00', '5–7'], ['#ff7e00', '4–5'], ['#ff0000', '<4 mg/L']],
    conductance: [['#00e400', '<500'], ['#ffff00', '500–1000'], ['#ff7e00', '1000–2000'], ['#ff0000', '>2000 µS/cm']],
    turbidity: [['#00e400', '<10'], ['#ffff00', '10–50'], ['#ff7e00', '50–100'], ['#ff0000', '>100 FNU']]}[param] || [];
  return swatches.map(([c, t]) => `<span class="chip"><span class="sw" style="background:${c}"></span>${t}</span>`).join(' ')
    + ' · faint = not measured here';
}

async function showSite(s) {
  $('w-detailcard').style.display = 'block';
  $('w-sitename').textContent = s.name;
  const meta = WATER_PARAMS[current];
  $('w-detailnote').textContent = meta.note;
  $('w-chart').innerHTML = 'Loading…';
  try {
    const d = await (await fetch(`/api/water/series/${s.site_id}?parameter=${current}&days=30`)).json();
    const pts = d.points || [];
    if (!pts.length) { $('w-chart').innerHTML = `<p class="meta" style="padding:14px">No recent ${meta.label} data at this site yet — it accumulates as our collector runs.</p>`; return; }
    Plotly.newPlot('w-chart', [{x: pts.map(p => p.ts), y: pts.map(p => p.value), mode: 'lines+markers',
      line: {color: '#2c6a9e'}, name: meta.label}],
      {margin: {t: 10, r: 10, b: 40, l: 48}, height: 300,
       yaxis: {title: meta.label + (meta.unit ? ` (${meta.unit})` : '')}},
      {displayModeBar: false, responsive: true});
  } catch (e) { $('w-chart').innerHTML = '<p class="meta" style="padding:14px">Could not load the chart.</p>'; }
  $('w-sitename').scrollIntoView({behavior: 'smooth', block: 'nearest'});
}

async function init() {
  map = L.map('w-map').setView([38.9, -80.5], 7);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 18, attribution: '© OpenStreetMap'}).addTo(map);
  try { SITES = (await (await fetch('/api/water/sites')).json()).sites || []; }
  catch (e) { $('w-count').textContent = ' · could not load sites'; return; }
  // param picker: only params that at least one site reports
  const present = new Set();
  SITES.forEach(s => Object.keys(s.latest).forEach(p => present.add(p)));
  const opts = ORDER.filter(p => present.has(p));
  current = opts.includes('ph') ? 'ph' : opts[0];
  $('w-param').innerHTML = opts.map(p => `<option value="${p}">${WATER_PARAMS[p].label}</option>`).join('');
  $('w-param').value = current;
  $('w-param').addEventListener('change', e => { current = e.target.value; draw(); });
  draw();
}
init();
