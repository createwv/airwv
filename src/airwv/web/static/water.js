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
  ecoli:       {label: 'E. coli (bacteria)', unit: 'MPN/100mL', note: 'Swim-safety indicator — spikes after rain / sewer overflows. Recreational single-sample limit ~235.',
                color: v => v < 126 ? '#00e400' : v < 235 ? '#ffff00' : v < 1000 ? '#ff7e00' : '#ff0000'},
  iron:        {label: 'Iron', unit: 'mg/L', note: 'Mining / acid-drainage indicator. Secondary drinking standard 0.3 mg/L.',
                color: v => v < 0.3 ? '#00e400' : v < 1 ? '#ffff00' : v < 3 ? '#ff7e00' : '#ff0000'},
  sulfate:     {label: 'Sulfate', unit: 'mg/L', note: 'Elevated by mining. Secondary standard 250 mg/L.',
                color: v => v < 250 ? '#00e400' : v < 500 ? '#ffff00' : '#ff0000'},
  nitrate:     {label: 'Nitrate', unit: 'mg/L', note: 'Nutrient (agriculture/sewage). Drinking-water limit 10 mg/L.',
                color: v => v < 5 ? '#00e400' : v < 10 ? '#ffff00' : '#ff0000'},
  tds:         {label: 'Total dissolved solids', unit: 'mg/L', note: 'Overall dissolved content. Secondary standard 500 mg/L.',
                color: v => v < 500 ? '#00e400' : v < 1000 ? '#ffff00' : '#ff0000'},
  aluminum:    {label: 'Aluminum', unit: 'mg/L', note: 'Acid-drainage metal.', color: v => v < 0.75 ? '#00e400' : v < 2 ? '#ffff00' : '#ff0000'},
  manganese:   {label: 'Manganese', unit: 'mg/L', note: 'Mining metal. Secondary standard 0.05 mg/L.', color: v => v < 0.05 ? '#00e400' : v < 0.3 ? '#ffff00' : '#ff0000'},
  discharge:   {label: 'Streamflow', unit: 'cfs', note: 'How much water is flowing past — context for everything else.', color: () => '#4a7fb0'},
  gage_height: {label: 'Gage height', unit: 'ft', note: 'River stage (height) at the gauge.', color: () => '#4a7fb0'},
};
const ORDER = ['ph', 'ecoli', 'do', 'conductance', 'iron', 'sulfate', 'nitrate', 'tds',
               'aluminum', 'manganese', 'turbidity', 'temperature', 'discharge', 'gage_height'];

let SITES = [], map, layer, current = 'ph', userMarker;
let coalLayer, COAL = null;

// WV DEP coal-mine NPDES discharge permits — mining ↔ water overlay.
async function toggleCoal(on) {
  if (!on) { if (coalLayer) { coalLayer.remove(); coalLayer = null; } return; }
  if (!COAL) {
    try { COAL = (await (await fetch('/api/coal-npdes?min_outlets=1')).json()).permits || []; }
    catch (e) { COAL = []; }
  }
  if (coalLayer) coalLayer.remove();
  coalLayer = L.markerClusterGroup
    ? L.markerClusterGroup({chunkedLoading: true, maxClusterRadius: 50})
    : L.layerGroup();
  COAL.forEach(p => {
    if (p.lat == null || p.lon == null) return;
    const r = Math.min(5 + Math.log2(p.outlets || 1) * 1.5, 12);
    const streams = (p.receiving_streams || []).slice(0, 5).map(esc).join('<br>· ');
    L.circleMarker([p.lat, p.lon], {radius: r, color: '#4a2c12', weight: 1, fillColor: '#8B4513', fillOpacity: 0.8})
      .bindPopup(`<b>${esc(p.operator)}</b> <small>${esc(p.permit)}</small>`
        + `<br>⛏️ <b>${p.outlets}</b> permitted discharge outlet${p.outlets === 1 ? '' : 's'}`
        + ` into <b>${p.stream_count}</b> stream${p.stream_count === 1 ? '' : 's'}`
        + (streams ? `<br><small>Receiving:<br>· ${streams}${p.stream_count > 5 ? '<br>· …' : ''}</small>` : '')
        + `<br><a href="${esc(p.effluent_url)}" target="_blank" rel="noopener">EPA ECHO effluent charts →</a>`)
      .addTo(coalLayer);
  });
  coalLayer.addTo(map);
}

function nearMe() {
  if (!navigator.geolocation) { $('w-note').textContent = 'geolocation not available on this device'; return; }
  navigator.geolocation.getCurrentPosition(p => {
    const lat = p.coords.latitude, lon = p.coords.longitude;
    if (userMarker) userMarker.remove();
    userMarker = L.marker([lat, lon], {zIndexOffset: 1000,
      icon: L.divIcon({className: '', html: '📍', iconSize: [26, 26], iconAnchor: [13, 26]})})
      .addTo(map).bindPopup('You are here').openPopup();
    map.setView([lat, lon], 11);
  }, () => { $('w-note').textContent = 'could not get your location'; }, {enableHighAccuracy: true, timeout: 10000});
}

function colorFor(site, param) {
  const l = site.latest[param];
  if (!l) return null;
  return (WATER_PARAMS[param].color)(l.value);
}

function draw() {
  if (layer) layer.remove();
  layer = L.markerClusterGroup
    ? L.markerClusterGroup({chunkedLoading: true, maxClusterRadius: 45, spiderfyOnMaxZoom: true})
    : L.layerGroup();
  const meta = WATER_PARAMS[current];
  let withVal = 0;
  SITES.forEach(s => {
    const l = s.latest[current];
    if (!l || s.lat == null || s.lon == null) return;   // only plot sites reporting this measure
    withVal++;
    const val = `${l.value}${meta.unit ? ' ' + meta.unit : ''}`;
    const when = l.ts ? new Date(l.ts).toLocaleDateString('en-US', {month: 'short', day: 'numeric', year: 'numeric'}) : '';
    L.circleMarker([s.lat, s.lon], {radius: 6, color: '#333', weight: 1, fillColor: colorFor(s, current), fillOpacity: 0.9})
      .bindPopup(`<b>${esc(s.name)}</b><br>${meta.label}: <b>${val}</b>`
        + (when ? `<br><small>latest sample: ${when}</small>` : '')
        + `<br><small>click to chart</small>`)
      .on('click', () => showSite(s)).addTo(layer);
  });
  layer.addTo(map);
  $('w-count').textContent = `· ${withVal} sites report ${meta.label.toLowerCase()}`;
  $('w-note').textContent = meta.note;
  $('w-legend').innerHTML = legend(current);
}

function legend(param) {
  if (['temperature', 'discharge', 'gage_height'].includes(param))
    return 'Each dot is a site reporting this measure. Click a dot to chart its history.';
  const swatches = {ph: [['#00e400', '6.5–9 (healthy)'], ['#ffff00', 'slightly off'], ['#ff0000', 'acidic/basic']],
    do: [['#00e400', '≥7'], ['#ffff00', '5–7'], ['#ff7e00', '4–5'], ['#ff0000', '<4 mg/L']],
    conductance: [['#00e400', '<500'], ['#ffff00', '500–1000'], ['#ff7e00', '1000–2000'], ['#ff0000', '>2000 µS/cm']],
    turbidity: [['#00e400', '<10'], ['#ffff00', '10–50'], ['#ff7e00', '50–100'], ['#ff0000', '>100 FNU']],
    ecoli: [['#00e400', '<126'], ['#ffff00', '126–235'], ['#ff7e00', '235–1000'], ['#ff0000', '>1000 (unsafe)']],
    iron: [['#00e400', '<0.3'], ['#ffff00', '0.3–1'], ['#ff7e00', '1–3'], ['#ff0000', '>3 mg/L']],
    sulfate: [['#00e400', '<250'], ['#ffff00', '250–500'], ['#ff0000', '>500 mg/L']],
    nitrate: [['#00e400', '<5'], ['#ffff00', '5–10'], ['#ff0000', '>10 mg/L (limit)']],
    tds: [['#00e400', '<500'], ['#ffff00', '500–1000'], ['#ff0000', '>1000 mg/L']],
    aluminum: [['#00e400', '<0.75'], ['#ffff00', '0.75–2'], ['#ff0000', '>2 mg/L']],
    manganese: [['#00e400', '<0.05'], ['#ffff00', '0.05–0.3'], ['#ff0000', '>0.3 mg/L']]}[param] || [];
  return swatches.map(([c, t]) => `<span class="chip"><span class="sw" style="background:${c}"></span>${t}</span>`).join(' ');
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
  $('w-nearme').addEventListener('click', nearMe);
  $('w-coal').addEventListener('change', e => toggleCoal(e.target.checked));
  draw();
}
init();
