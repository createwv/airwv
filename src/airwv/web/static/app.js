const $ = id => document.getElementById(id);
const j = async u => (await fetch(u)).json();
const busy = (id,on) => { const el=$(id); if(el) el.classList.toggle('on', on); };
const COLORS = ['#3b2a6b','#e07b00','#1b9e77','#d62728','#7570b3','#17becf','#b8860b'];
let map, markers = {}, allSensors = [], GUIDE = null;
let chartSet = new Set();   // sensor ids currently plotted (click a row or a map dot to toggle)
function toggleChart(sid){
  if (chartSet.has(sid)) chartSet.delete(sid); else chartSet.add(sid);
  // update the tree row(s) in place (don't rebuild — that would collapse open groups)
  document.querySelectorAll(`.srow[data-sid="${sid}"]`).forEach(el => el.classList.toggle('on', chartSet.has(sid)));
  redrawSensors(); render();
}

const GUIDE_META = {pm2_5:['PM2.5',' µg/m³'], pm10:['PM10',' µg/m³'], voc:['VOC',' (relative index)']};
function guideBands(field){ return field==='pm2_5'?GUIDE?.pm2_5 : field==='pm10'?GUIDE?.pm10 : field==='voc'?GUIDE?.voc : null; }
async function loadGuide(){ GUIDE = await j('/api/guide'); renderGuide(); }
function renderGuide(){
  if (!GUIDE) return;
  const field = $('field').value, arr = guideBands(field);
  if (!arr){
    $('guide').innerHTML = `<div class="note">No standard health thresholds for this metric — watch trends and cross-sensor comparisons instead.</div>`;
    return;
  }
  const [label, unit] = GUIDE_META[field];
  const chips = arr.map((b,i) => {
    const lo = i === 0 ? 0 : arr[i-1].max;
    const range = b.max == null ? `${lo}+` : `${lo}–${b.max}`;
    return `<span class="chip"><span class="sw" style="background:${b.color}"></span>${b.label} (${range}${unit})</span>`;
  }).join('');
  $('guide').innerHTML = `<b style="flex-basis:100%">${label}${unit}</b>` + chips +
    (field==='voc' ? `<div class="note">${GUIDE.voc_note}</div>` : '');
}
function bandShapes(field, yMax){
  const arr = guideBands(field);
  if (!arr) return [];
  const shapes = [];
  arr.forEach((b,i) => {
    const lo = i === 0 ? 0 : arr[i-1].max;
    const hi = b.max == null ? yMax : Math.min(b.max, yMax);
    if (hi <= lo) return;
    shapes.push({type:'rect', xref:'paper', x0:0, x1:1, yref:'y', y0:lo, y1:hi,
      fillcolor:b.color, opacity:0.10, line:{width:0}, layer:'below'});
  });
  return shapes;
}

async function loadSensors(){
  allSensors = await j('/api/sensors');
  // default: chart EWV Glasgow 1 (falls back to the first community sensor)
  const glasgow = allSensors.find(s => /glasgow/i.test(s.name)) ||
                  allSensors.find(s => s.kind === 'community' && s.lat != null);
  if (glasgow) chartSet.add(glasgow.sensor_id);
  drawMap(allSensors);
  render();
}
async function loadCoverage(){
  const c = await j('/api/coverage');
  if (c.first_ts) $('coverage').textContent =
    `Showing all data · ${Number(c.count).toLocaleString()} readings · first ${c.first_ts.slice(0,10)} → last ${c.last_ts.slice(0,10)}`;
}
const layerState = {community:true, reference:true, sources:true, reports:true, ozone:false,
  echo:false, dep:false, mine:false, wells:false, wellOrphan:true, wellOperator:true, wellNearOnly:false,
  regions:{}, cats:{},
  echoStatus:{significant_violation:true, violation:true, compliant:false},
  depStage:{requested:true, construction:true, approved:true},
  mineStage:{new:true, active:true, inactive:false}};
// ⭐ My Sensors — a personal follow-list persisted in the browser (accounts later)
let mySensors = new Set(JSON.parse(localStorage.getItem('airwv_my_sensors') || '[]'));
function toggleFollow(sid){
  mySensors.has(sid) ? mySensors.delete(sid) : mySensors.add(sid);
  localStorage.setItem('airwv_my_sensors', JSON.stringify([...mySensors]));
  buildLayers();
}
let userMarker;
function drawMap(sensors){
  if (!map){
    map = L.map('map').setView([38.35, -81.6], 8);
    const tile = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
      {maxZoom:18, attribution:'© OpenStreetMap'}).addTo(map);
    tile.on('load', () => busy('b-map', false));      // hide spinner once tiles are in
    setTimeout(() => busy('b-map', false), 5000);     // fallback
    window.AIRWV_MAP = map;                            // reporting.js uses this to drop the location pin
    window.AIRWV_ON_REPORT = loadReports;             // refresh the report layer after a submission
    // lazily fill a facility popup's "measured water nearby" box the first time it opens
    map.on('popupopen', e => {
      const box = e.popup.getElement().querySelector('.fac-wq[data-pending]');
      if (box) fillFacWater(box).then(() => e.popup.update());
    });
  }
  redrawSensors();
  const pts = allSensors.filter(s => s.kind !== 'reference' && s.lat != null).map(s => [s.lat, s.lon]);
  if (pts.length) map.fitBounds(pts, {padding:[30,30], maxZoom:11});
  loadSources();
  loadReports();
  loadFacilities();
  loadDepPermits();
  loadDepMining();
}
function redrawSensors(){
  if (sensorLayer) sensorLayer.remove();
  if (refLayer) refLayer.remove();
  if (ozoneLayer) ozoneLayer.remove();
  sensorLayer = L.layerGroup();
  refLayer = L.layerGroup();
  ozoneLayer = L.layerGroup();
  // ozone monitors (EPA reference) as square markers, colored by ozone AQI band
  if (layerState.ozone) allSensors.forEach(x => {
    if (x.latest_ozone == null || x.lat == null) return;
    L.marker([x.lat, x.lon], {icon: L.divIcon({className:'', iconSize:[15,15], iconAnchor:[7,7],
      html:`<div class="o3mk" style="background:${x.ozone_color||'#9e9e9e'}"></div>`})})
      .bindPopup(`<b>${x.name}</b><br><i>EPA ozone monitor</i><br>latest O₃: <b>${x.latest_ozone} ppb</b>`
        + `<br><small>select the Ozone metric, then click to chart</small>`)
      .on('click', () => toggleChart(x.sensor_id)).addTo(ozoneLayer);
  });
  allSensors.forEach(x => {
    if (x.lat == null || x.lon == null) return;
    const charted = chartSet.has(x.sensor_id);
    // charted markers get a gold highlight ring so map ⇔ chart stay in sync
    const ring = charted ? {color:'#c9992f', weight:4} : null;
    const pop = `<b>${x.name}</b>${x.kind==='reference'?'<br><i>reference monitor (EPA/AirNow)</i>':''}`+
      `<br>latest PM2.5: ${x.latest_pm2_5 ?? '—'}<br>${Number(x.count).toLocaleString()} readings`+
      `<br><small>${charted?'✓ charted — click to remove':'click to add to chart'}</small>`;
    if (x.kind === 'reference') {
      if (!layerState.reference) return;
      L.circleMarker([x.lat, x.lon], {radius:8, color:(ring?ring.color:'#111'), weight:(ring?ring.weight:3), fillColor:x.color, fillOpacity:0.85})
        .bindPopup(pop).on('click', () => toggleChart(x.sensor_id)).addTo(refLayer);
    } else {
      if (!layerState.community || layerState.regions[x.region] === false) return;
      L.circleMarker([x.lat, x.lon], {radius:9, color:(ring?ring.color:'#333'), weight:(ring?ring.weight:1), fillColor:x.color, fillOpacity:0.9})
        .bindPopup(pop).on('click', () => toggleChart(x.sensor_id)).addTo(sensorLayer);
    }
  });
  sensorLayer.addTo(map);
  refLayer.addTo(map);
  if (layerState.ozone) ozoneLayer.addTo(map);
}
let sensorLayer, refLayer, ozoneLayer;
// reference monitors are now drawn live in drawMap() (ringed circles, current PM2.5)
const SRC_ICON = {power:'⚡', chemical:'⚗️', oil_gas:'🛢️', materials:'⛏️', waste:'🗑️',
  water_discharge:'💧', other:'🏭'};
const SRC_LABEL = {power:'Power plant', chemical:'Chemical', oil_gas:'Oil & gas',
  materials:'Metals / mining / materials', waste:'Waste',
  water_discharge:'Water discharge (NPDES)', other:'Other TRI facility'};
let sourceLayer, allSources_ = [], srcDisclaimer = '';
async function loadSources(){
  const data = await j('/api/sources');
  allSources_ = data.sources; srcDisclaimer = data.disclaimer || '';
  redrawSources();
  buildLayers();
  $('srclist').innerHTML = allSources_.filter(s=>s.lat!=null)
    .map(s=>`<option value="${s.name.replace(/"/g,'&quot;')}">`).join('');
  // deep link from the Sources page: /air?src=<facility name>
  const wantSrc = new URLSearchParams(location.search).get('src');
  if (wantSrc && allSources_.some(s => s.name === wantSrc)){
    $('srcpick').value = wantSrc; showProximity(wantSrc);
    document.querySelector('#proxtable').scrollIntoView({behavior:'smooth', block:'center'});
  }
}
// ---- Source-proximity: pick a polluter, see the sensors around it by distance + bearing ----
const DIRS = ['N','NE','E','SE','S','SW','W','NW'];
function haversineMi(la1,lo1,la2,lo2){
  const R=6371, rad=x=>x*Math.PI/180;
  const dp=rad(la2-la1), dl=rad(lo2-lo1);
  const h=Math.sin(dp/2)**2 + Math.cos(rad(la1))*Math.cos(rad(la2))*Math.sin(dl/2)**2;
  return 2*R*Math.asin(Math.sqrt(h))*0.621371;
}
function bearing8(la1,lo1,la2,lo2){
  const rad=x=>x*Math.PI/180;
  const y=Math.sin(rad(lo2-lo1))*Math.cos(rad(la2));
  const x=Math.cos(rad(la1))*Math.sin(rad(la2))-Math.sin(rad(la1))*Math.cos(rad(la2))*Math.cos(rad(lo2-lo1));
  return DIRS[Math.round(((Math.atan2(y,x)*180/Math.PI+360)%360)/45)%8];
}
const zone = mi => mi<1 ? 'near-field (<1 mi)' : mi<3 ? 'vicinity (1–3 mi)' : mi<10 ? 'downwind range (3–10 mi)' : 'far';
let proxRings = null, proxNearest = [], windRoses = [];
async function loadWindRoses(){ try { windRoses = (await j('/api/wind-roses')).stations || []; } catch(e){} }
const opposite = d => DIRS[(DIRS.indexOf(d)+4)%8];
function nearestStation(lat,lon){
  let best=null; windRoses.forEach(s=>{ const d=haversineMi(lat,lon,s.lat,s.lon); if(!best||d<best._d) best={...s,_d:d}; }); return best;
}
const prevailing = rose => Object.keys(rose).reduce((a,b)=> rose[b]>rose[a]?b:a);
function showProximity(name){
  const src = allSources_.find(s => s.name === name && s.lat != null);
  const tb = document.querySelector('#proxtable tbody');
  if (!src){ tb.innerHTML='<tr><td colspan="6" style="color:#999">Pick a pollution source above.</td></tr>';
    $('chartnear').disabled=true; $('proxsector').textContent=''; if(proxRings){proxRings.remove();proxRings=null;} return; }
  const weighted = $('windweight').checked && windRoses.length;
  const station = weighted ? nearestStation(src.lat, src.lon) : null;
  const rose = station ? station.rose : null;
  const near = allSensors.filter(s => s.lat != null).map(s => {
    const mi = haversineMi(src.lat,src.lon,s.lat,s.lon), dir = bearing8(src.lat,src.lon,s.lat,s.lon);
    // wind FROM the opposite of the sensor's bearing carries the plume toward it
    const dwFreq = rose ? (rose[opposite(dir)] || 0) : null;
    const score = weighted ? dwFreq * Math.exp(-mi/3) : 1/(mi+0.3);
    return {...s, mi, dir, dwFreq, score};
  }).sort((a,b) => b.score - a.score);
  proxNearest = near.filter(s => s.kind === 'community').slice(0,6);
  $('chartnear').disabled = proxNearest.length === 0;
  tb.innerHTML = near.slice(0,14).map(s => `<tr>
    <td>${s.name}</td><td>${s.kind==='reference'?'◎ reference':'● community'}</td>
    <td>${s.mi.toFixed(1)} mi</td><td>${s.dir}</td>
    <td>${s.dwFreq==null?'—':Math.round(s.dwFreq*100)+'%'}</td>
    <td style="color:${s.mi<1?'#a00':s.mi<3?'#7a5b12':'#666'}">${zone(s.mi)}</td></tr>`).join('');
  if (weighted && station){
    const p = prevailing(rose);
    $('proxsector').innerHTML = `<b>🌀 Weighted by wind</b> — nearest station <b>${station.name}</b>, wind usually <b>from ${p}</b> (${Math.round(rose[p]*100)}%). Ranked by downwind exposure × proximity; "Downwind" = how often the wind carries the plume toward that sensor.`;
  } else {
    const bySec={};
    near.filter(s=>s.kind==='community').forEach(s=>{ if(!bySec[s.dir]||s.mi<bySec[s.dir].mi) bySec[s.dir]=s; });
    $('proxsector').innerHTML = '<b>Nearest community sensor by direction:</b> ' +
      (DIRS.filter(d=>bySec[d]).map(d=>`${d} ${bySec[d].name} (${bySec[d].mi.toFixed(1)}mi)`).join(' · ') || '—');
  }
  if (proxRings) proxRings.remove();
  proxRings = L.layerGroup();
  [1,3].forEach(mi => L.circle([src.lat,src.lon], {radius:mi*1609.34, color:'#a00', weight:1, fill:false, dashArray:'4'}).addTo(proxRings));
  L.marker([src.lat,src.lon], {icon:L.divIcon({className:'', html:'🎯', iconSize:[24,24], iconAnchor:[12,12]})})
    .bindPopup(`<b>${src.name}</b><br>proximity center`).addTo(proxRings);
  proxRings.addTo(map);
  if (map) map.setView([src.lat, src.lon], 10);
}
function initProximity(){
  loadWindRoses();
  $('srcpick').addEventListener('change', e => showProximity(e.target.value.trim()));
  $('windweight').addEventListener('change', () => showProximity($('srcpick').value.trim()));
  $('chartnear').addEventListener('click', () => {
    proxNearest.forEach(s => chartSet.add(s.sensor_id));
    proxNearest.forEach(s => document.querySelectorAll(`.srow[data-sid="${s.sensor_id}"]`).forEach(el => el.classList.add('on')));
    redrawSensors(); render();
  });
}
// Compact water-quality bands (mirror of the Water page) so a facility popup can
// color-code the measured water sampled nearby.
const WQ = {
  ph:          {label: 'pH', unit: '', color: v => (v >= 6.5 && v <= 9) ? '#137333' : (v >= 6 && v <= 9.5 ? '#b8860b' : '#c0392b')},
  conductance: {label: 'Conductivity', unit: 'µS/cm', color: v => v < 500 ? '#137333' : v < 1000 ? '#b8860b' : v < 2000 ? '#e07b00' : '#c0392b'},
  iron:        {label: 'Iron', unit: 'mg/L', color: v => v < 0.3 ? '#137333' : v < 1 ? '#b8860b' : v < 3 ? '#e07b00' : '#c0392b'},
  aluminum:    {label: 'Aluminum', unit: 'mg/L', color: v => v < 0.75 ? '#137333' : v < 2 ? '#b8860b' : '#c0392b'},
  manganese:   {label: 'Manganese', unit: 'mg/L', color: v => v < 0.05 ? '#137333' : v < 0.3 ? '#b8860b' : '#c0392b'},
  sulfate:     {label: 'Sulfate', unit: 'mg/L', color: v => v < 250 ? '#137333' : v < 500 ? '#b8860b' : '#c0392b'},
  selenium:    {label: 'Selenium', unit: 'µg/L', color: v => v < 5 ? '#137333' : v < 20 ? '#b8860b' : v < 50 ? '#e07b00' : '#c0392b'},
  nitrate:     {label: 'Nitrate', unit: 'mg/L', color: v => v < 5 ? '#137333' : v < 10 ? '#b8860b' : '#c0392b'},
  ecoli:       {label: 'E. coli', unit: 'MPN/100mL', color: v => v < 126 ? '#137333' : v < 235 ? '#b8860b' : '#c0392b'},
};
const WQ_ORDER = ['selenium', 'iron', 'aluminum', 'manganese', 'sulfate', 'conductance', 'ph', 'nitrate', 'ecoli'];
function renderFacWater(sites) {
  const withVals = sites.filter(s => WQ_ORDER.some(k => s.latest[k]));
  if (!withVals.length) return '<small class="meta">No water samples within ~5 mi on record.</small>';
  let html = '<div class="cn-wqbox"><small><b>💧 Measured water nearby</b></small>';
  withVals.slice(0, 2).forEach(s => {
    const chips = WQ_ORDER.filter(k => s.latest[k]).map(k => {
      const l = s.latest[k], m = WQ[k], col = m.color(l.value);
      return `<span class="wq-chip" style="border-color:${col}"><b style="color:${col}">${l.value}${m.unit ? ' ' + m.unit : ''}</b> ${m.label}</span>`;
    }).join(' ');
    if (chips) html += `<div style="margin:3px 0"><small>${s.name} <span class="meta">${s.mi} mi</span></small><br>${chips}</div>`;
  });
  return html + '</div>';
}
async function fillFacWater(box) {
  box.removeAttribute('data-pending');
  box.innerHTML = '<small class="meta">loading nearby measurements…</small>';
  try {
    const sites = (await j(`/api/water/near?lat=${box.dataset.lat}&lon=${box.dataset.lon}&km=8&limit=4`)).sites || [];
    box.innerHTML = renderFacWater(sites);
  } catch (_) { box.innerHTML = '<small class="meta">measured water unavailable</small>'; }
}

// ---- EPA ECHO compliance layer (WV major facilities, colored by status) ----
const ECHO_COLOR = {significant_violation:'#c0392b', violation:'#e07b00', compliant:'#137333', unknown:'#8a94a0'};
const ECHO_LABEL = {significant_violation:'Significant violation', violation:'In violation',
  compliant:'No violation', unknown:'Not tracked'};
const ECHO_PROG = {air:'🌫️ Air', water:'💧 Water', waste:'♻️ Waste',
  'drinking-water':'🚰 Drinking water', 'toxics-release':'☢️ Toxics'};
let echoLayer, allFacilities_ = [], echoMeta = {};
async function loadFacilities(){
  try { const d = await j('/api/facilities'); allFacilities_ = d.facilities || []; echoMeta = d; }
  catch(e){ allFacilities_ = []; }
  redrawEcho(); buildLayers();
}
function redrawEcho(){
  if (echoLayer) echoLayer.remove();
  echoLayer = L.layerGroup();
  if (layerState.echo) allFacilities_.forEach(f => {
    if (f.lat == null || f.lon == null) return;
    if (layerState.echoStatus[f.status] === false) return;
    const col = ECHO_COLOR[f.status] || ECHO_COLOR.unknown;
    const r = f.status === 'significant_violation' ? 8 : f.status === 'violation' ? 7 : 5;
    const progs = (f.programs||[]).map(p=>ECHO_PROG[p]||p).join(' · ');
    // water/drinking-water dischargers get a lazily-loaded measured-water block
    const water = (f.programs||[]).some(p=>p==='water'||p==='drinking-water')
      ? `<div class="fac-wq" data-pending data-lat="${f.lat}" data-lon="${f.lon}"></div>` : '';
    L.circleMarker([f.lat, f.lon], {radius:r, color:'#333', weight:1, fillColor:col, fillOpacity:0.85})
      .bindPopup(`<b>${f.name}</b><br><span style="color:${col};font-weight:600">⚖️ ${ECHO_LABEL[f.status]||''}</span>`+
        `<br><small>${[f.city, f.county && f.county+' Co.'].filter(Boolean).join(', ')}</small>`+
        (progs?`<br><small>${progs}</small>`:'')+
        (f.last_inspection?`<br><small>last inspected ${f.last_inspection}</small>`:'')+
        water+
        `<br><a href="${f.echo_url}" target="_blank" rel="noopener">EPA ECHO report →</a>`)
      .addTo(echoLayer);
  });
  echoLayer.addTo(map);
}
// ---- WV DEP oil & gas permit pipeline (forward-looking lifecycle) ----
const DEP_COLOR = {requested:'#8e44ad', approved:'#2c7fb8', construction:'#e07b00', other:'#8a94a0'};
const DEP_LABEL = {requested:'Requested (pending)', approved:'Approved (permit issued)',
  construction:'Under construction', other:'Other'};
let depLayer, allPermits_ = [], depMeta = {};
async function loadDepPermits(){
  try { const d = await j('/api/dep-permits'); allPermits_ = d.permits || []; depMeta = d; }
  catch(e){ allPermits_ = []; }
  redrawDep(); buildLayers();
}
function redrawDep(){
  if (depLayer) depLayer.remove();
  depLayer = L.layerGroup();
  if (layerState.dep) allPermits_.forEach(p => {
    if (p.lat == null || p.lon == null) return;
    if (layerState.depStage[p.stage] === false) return;
    const col = DEP_COLOR[p.stage] || DEP_COLOR.other;
    const when = p.issue_date || p.received_date;
    L.circleMarker([p.lat, p.lon], {radius:6, color:'#333', weight:1, fillColor:col, fillOpacity:0.85})
      .bindPopup(`<b>${p.operator||'Operator unknown'}</b>`+
        `<br><span style="color:${col};font-weight:600">🛢️ ${DEP_LABEL[p.stage]||''}</span>`+
        `<br><small>${[p.county && p.county+' Co.', p.well_type].filter(Boolean).join(' · ')}</small>`+
        (p.formation?`<br><small>${p.formation}${p.marcellus?' · Marcellus':''}</small>`:'')+
        (when?`<br><small>${p.issue_date?'permit issued':'application'} ${when}</small>`:'')+
        (p.link?`<br><a href="${p.link}" target="_blank" rel="noopener">WV DEP record →</a>`:''))
      .addTo(depLayer);
  });
  depLayer.addTo(map);
}
// ---- WV DEP mining permits (coal/mineral — active + upcoming) ----
const MINE_COLOR = {new:'#e67e22', active:'#8B4513', inactive:'#95a5a6', other:'#8a94a0'};
const MINE_LABEL = {new:'New / not yet started', active:'Active / renewed', inactive:'Inactive (idle)', other:'Other'};
let mineLayer, allMines_ = [], mineMeta = {};
async function loadDepMining(){
  try { const d = await j('/api/dep-mining'); allMines_ = d.mines || []; mineMeta = d; }
  catch(e){ allMines_ = []; }
  redrawMines(); buildLayers();
}
function redrawMines(){
  if (mineLayer) mineLayer.remove();
  mineLayer = L.layerGroup();
  if (layerState.mine) allMines_.forEach(m => {
    if (m.lat == null || m.lon == null) return;
    if (layerState.mineStage[m.stage] === false) return;
    const col = MINE_COLOR[m.stage] || MINE_COLOR.other;
    const acres = m.acres_disturbed ? `<br><small>${m.acres_disturbed} acres disturbed${m.acres_reclaimed?` · ${m.acres_reclaimed} reclaimed`:''}</small>` : '';
    L.circleMarker([m.lat, m.lon], {radius:6, color:'#333', weight:1, fillColor:col, fillOpacity:0.85})
      .bindPopup(`<b>${m.operator||'Operator unknown'}</b>`+
        `<br><span style="color:${col};font-weight:600">⛏️ ${MINE_LABEL[m.stage]||''}</span>`+
        `<br><small>${[m.type, m.facility].filter(Boolean).join(' · ')}</small>`+
        acres+
        (m.inspection_status?`<br><small>${m.inspection_status}</small>`:'')+
        (m.issue_date?`<br><small>permit ${m.issue_date}</small>`:''))
      .addTo(mineLayer);
  });
  mineLayer.addTo(map);
}
// ---- Abandoned & orphaned gas wells (WV DEP — the Rutledge story) ----
// Large (~15k), so the layer is lazy: fetched only when first switched on.
let wellLayer, allWells_ = [], wellsLoaded = false, wellMeta = {};
async function loadAbandonedWells(){
  if (!wellsLoaded){
    try { const d = await j('/api/abandoned-wells'); allWells_ = d.wells || []; wellMeta = d; wellsLoaded = true; }
    catch(e){ allWells_ = []; }
    buildLayers();   // refresh the tree with real counts + sub-rows
  }
  redrawWells();
}
function redrawWells(){
  if (wellLayer) wellLayer.remove();
  wellLayer = L.markerClusterGroup
    ? L.markerClusterGroup({chunkedLoading:true, maxClusterRadius:50, disableClusteringAtZoom:14})
    : L.layerGroup();
  if (layerState.wells) allWells_.forEach(w => {
    if (w.lat == null || w.lon == null) return;
    if (w.orphan ? layerState.wellOrphan===false : layerState.wellOperator===false) return;
    if (layerState.wellNearOnly && !w.near_homes) return;
    const col = w.orphan ? '#c0392b' : '#7a6a55';
    const near = w.near_homes;
    const rec = `http://www.wvgs.wvnet.edu/oginfo/pipeline/pipeline2.asp?txtsearchapi=47${(w.id||'').replace('-','')}`;
    // near-homes wells pop (bigger, opaque, dark ring); remote ones fade back
    const prox = w.near_homes ? `🏠 <b>within ${w.nearest_building_m} m of a building</b>`
      : (w.nearest_building_m != null ? `~${w.nearest_building_m} m to nearest building` : 'remote (no building within ~1 km)');
    L.circleMarker([w.lat, w.lon], {radius: near?6:3.5, color: near?'#400':'#333',
      weight: near?1.2:0.5, fillColor:col, fillOpacity: near?0.95:0.55})
      .bindPopup(`<b>Abandoned gas well</b> <small>${w.id||''}</small>`+
        `<br><span style="color:${col};font-weight:600">${w.orphan?'🚱 Orphan — no known operator (state to plug)':'⚠️ Abandoned'}</span>`+
        (w.operator?`<br><small>${w.operator}</small>`:'')+
        (w.county?`<br><small>${w.county} County</small>`:'')+
        `<br><small>${prox}</small>`+
        `<br><small>Leaking wells can vent natural gas / H2S near homes.</small>`+
        `<br><a href="${rec}" target="_blank" rel="noopener">WV DEP well record →</a>`)
      .addTo(wellLayer);
  });
  wellLayer.addTo(map);
}
function redrawSources(){
  if (sourceLayer) sourceLayer.remove();
  sourceLayer = L.markerClusterGroup
    ? L.markerClusterGroup({chunkedLoading:true, maxClusterRadius:45, spiderfyOnMaxZoom:true})
    : L.layerGroup();
  if (layerState.sources) allSources_.forEach(s => {
    if (s.lat == null || s.lon == null) return;
    const cat = s.category || 'other';
    if (layerState.cats[cat] === false) return;
    L.marker([s.lat, s.lon], {icon: L.divIcon({className:'', html:SRC_ICON[cat]||'🏭',
      iconSize:[22,22], iconAnchor:[11,11]})})
      .bindPopup(`<b>${s.name}</b>${s.state?` <small>(${s.state})</small>`:''}`+
        `<br><b style="color:#7a5b12">${SRC_LABEL[cat]||'Facility'}</b> · ${s.type}<br><i>${s.operator||''}</i>`+
        `<br><small>Documented public-record facility · ${s.citation||''}</small>`+
        `<br><small style="color:#a00">${srcDisclaimer}</small>`)
      .addTo(sourceLayer);
  });
  sourceLayer.addTo(map);
}
// ---- Collapsible map-layers tree (community by region · reference · sources by category) ----
function groupCount(arr, key){ const m={}; arr.forEach(x=>{ const k=x[key]; if(k) m[k]=(m[k]||0)+1; }); return m; }
function buildLayers(){
  const comm = allSensors.filter(s => s.kind==='community' && s.lat!=null);
  const ref = allSensors.filter(s => s.kind==='reference' && s.lat!=null);
  const rCounts = groupCount(comm,'region'), cCounts = groupCount(allSources_,'category');
  const regions = Object.keys(rCounts).sort();
  const cats = ['power','chemical','oil_gas','materials','waste','other'].filter(c=>cCounts[c]);
  regions.forEach(r=>{ if(!(r in layerState.regions)) layerState.regions[r]=true; });
  cats.forEach(c=>{ if(!(c in layerState.cats)) layerState.cats[c]=true; });
  const row = (attr,val,checked,label,cnt)=>
    `<label><input type="checkbox" data-${attr}="${val}" ${checked?'checked':''}> ${label} <span class="cnt">${cnt}</span></label>`;
  const srowHtml = s => `<div class="srow${chartSet.has(s.sensor_id)?' on':''}" data-sid="${s.sensor_id}">`+
    `<span class="star${mySensors.has(s.sensor_id)?' on':''}" data-star="${s.sensor_id}" title="follow">${mySensors.has(s.sensor_id)?'★':'☆'}</span>${s.name}</div>`;
  // community: each region is a <details> (visibility checkbox) holding clickable sensor rows
  const byRegion = {};
  comm.forEach(s => { (byRegion[s.region] = byRegion[s.region] || []).push(s); });
  const regionBlocks = regions.map(r => {
    const rows = (byRegion[r]||[]).sort((a,b)=>a.name.localeCompare(b.name)).map(srowHtml).join('');
    return `<details><summary><input type="checkbox" data-region="${r}" ${layerState.regions[r]?'checked':''}> ${r} <span class="cnt">${rCounts[r]}</span></summary><div class="children">${rows}</div></details>`;
  }).join('');
  // ⭐ pinned follow-list at the top
  const followed = comm.filter(s => mySensors.has(s.sensor_id)).sort((a,b)=>a.name.localeCompare(b.name));
  const myBlock = followed.length
    ? `<details open><summary>⭐ My Sensors <span class="cnt">${followed.length}</span></summary><div class="children">${followed.map(srowHtml).join('')}</div></details>`
    : '';
  const catRows = cats.map(c=>row('cat',c,layerState.cats[c],`${SRC_ICON[c]} ${SRC_LABEL[c]}`,cCounts[c])).join('');
  // ⚖️ EPA ECHO compliance — WV major facilities, colored by status
  const eCounts = groupCount(allFacilities_,'status');
  const eStatuses = ['significant_violation','violation','compliant'].filter(s=>eCounts[s]);
  const dot = s => `<span style="color:${ECHO_COLOR[s]}">●</span>`;
  const echoRows = eStatuses.map(s=>row('echo',s,layerState.echoStatus[s]!==false,`${dot(s)} ${ECHO_LABEL[s]}`,eCounts[s])).join('');
  const echoTotal = allFacilities_.length;
  // 🛢️ WV DEP oil & gas permit pipeline, colored by lifecycle stage
  const dCounts = groupCount(allPermits_,'stage');
  const dStages = ['requested','construction','approved'].filter(s=>dCounts[s]);
  const ddot = s => `<span style="color:${DEP_COLOR[s]}">●</span>`;
  const depRows = dStages.map(s=>row('dep',s,layerState.depStage[s]!==false,`${ddot(s)} ${DEP_LABEL[s]}`,dCounts[s])).join('');
  const depTotal = allPermits_.length;
  // ⛏️ WV DEP mining permits, colored by lifecycle stage
  const mCounts = groupCount(allMines_,'stage');
  const mStages = ['new','active','inactive'].filter(s=>mCounts[s]);
  const mdot = s => `<span style="color:${MINE_COLOR[s]}">●</span>`;
  const mineRows = mStages.map(s=>row('mine',s,layerState.mineStage[s]!==false,`${mdot(s)} ${MINE_LABEL[s]}`,mCounts[s])).join('');
  const mineTotal = allMines_.length;
  // 🛢️ abandoned/orphan wells — lazy; sub-rows appear once loaded
  const wellsTotal = wellMeta.count || 0, orphanN = wellMeta.orphans || 0;
  const nearN = wellMeta.near_homes || 0;
  const wellSubs = wellsLoaded
    ? row('well','orphan',layerState.wellOrphan!==false,'<span style="color:#c0392b">●</span> Orphan (no operator)',orphanN)
      + row('well','operator',layerState.wellOperator!==false,'<span style="color:#7a6a55">●</span> Has operator',wellsTotal-orphanN)
      + `<label style="border-top:1px solid #eee;margin-top:2px;padding-top:3px"><input type="checkbox" id="L-wellnear" ${layerState.wellNearOnly?'checked':''}> 🏠 Near homes only <span class="cnt">${nearN}</span></label>`
    : '<label class="meta" style="padding:3px 6px">switch on to load ~15k wells…</label>';
  $('layers').innerHTML =
    `<b style="font-size:12px;color:#555">Sensors &amp; layers <span class="cnt">(★ to follow · click to chart)</span></b>`+
    myBlock+
    `<details open><summary><input type="checkbox" id="L-community" ${layerState.community?'checked':''}> ● Community sensors <span class="cnt">${comm.length}</span></summary><div class="children">${regionBlocks}</div></details>`+
    `<label style="align-self:center"><input type="checkbox" id="L-reference" ${layerState.reference?'checked':''}> ◎ Reference monitors <span class="cnt">${ref.length}</span></label>`+
    `<label style="align-self:center"><input type="checkbox" id="L-ozone" ${layerState.ozone?'checked':''}> ◆ Ozone monitors (EPA) <span class="cnt">${allSensors.filter(s=>s.latest_ozone!=null).length}</span></label>`+
    `<label style="align-self:center"><input type="checkbox" id="L-reports" ${layerState.reports!==false?'checked':''}> 📣 Community reports <span class="cnt">${allReports.length}</span></label>`+
    `<details><summary><input type="checkbox" id="L-sources" ${layerState.sources?'checked':''}> 🏭 Pollution sources <span class="cnt">${allSources_.length}</span></summary><div class="children">${catRows}</div></details>`+
    (echoTotal?`<details><summary><input type="checkbox" id="L-echo" ${layerState.echo?'checked':''}> ⚖️ Compliance (EPA ECHO) <span class="cnt">${echoTotal}</span></summary><div class="children">${echoRows}</div></details>`:'')+
    (depTotal?`<details><summary><input type="checkbox" id="L-dep" ${layerState.dep?'checked':''}> 🛢️ O&amp;G permit pipeline (WV DEP) <span class="cnt">${depTotal}</span></summary><div class="children">${depRows}</div></details>`:'')+
    (mineTotal?`<details><summary><input type="checkbox" id="L-mine" ${layerState.mine?'checked':''}> ⛏️ Mining permits (WV DEP) <span class="cnt">${mineTotal}</span></summary><div class="children">${mineRows}</div></details>`:'')+
    `<details><summary><input type="checkbox" id="L-wells" ${layerState.wells?'checked':''}> 🛢️ Abandoned wells (WV DEP) <span class="cnt">${wellsTotal || '~15k'}</span></summary><div class="children">${wellSubs}</div></details>`;
  // checkboxes in a <summary> shouldn't toggle its open/close
  $('layers').querySelectorAll('summary input[type=checkbox]').forEach(cb=> cb.addEventListener('click', e=>e.stopPropagation()));
  // updates happen in place (no rebuild) so open groups stay open
  $('L-community').onchange = e=>{ layerState.community=e.target.checked;
    regions.forEach(r=>layerState.regions[r]=e.target.checked);
    $('layers').querySelectorAll('[data-region]').forEach(cb=>{cb.checked=e.target.checked;cb.indeterminate=false;}); redrawSensors(); };
  $('L-reference').onchange = e=>{ layerState.reference=e.target.checked; redrawSensors(); };
  $('L-ozone').onchange = e=>{ layerState.ozone=e.target.checked; redrawSensors(); };
  $('L-reports').onchange = e=>{ layerState.reports=e.target.checked; redrawReports(); };
  $('L-sources').onchange = e=>{ layerState.sources=e.target.checked;
    cats.forEach(c=>layerState.cats[c]=e.target.checked);
    $('layers').querySelectorAll('[data-cat]').forEach(cb=>cb.checked=e.target.checked); redrawSources(); };
  $('layers').querySelectorAll('[data-region]').forEach(cb=> cb.onchange=e=>{
    layerState.regions[e.target.dataset.region]=e.target.checked;
    layerState.community=regions.some(r=>layerState.regions[r]); redrawSensors(); syncParents(regions,cats); });
  $('layers').querySelectorAll('[data-cat]').forEach(cb=> cb.onchange=e=>{
    layerState.cats[e.target.dataset.cat]=e.target.checked;
    layerState.sources=cats.some(c=>layerState.cats[c]); redrawSources(); syncParents(regions,cats); });
  if($('L-echo')) $('L-echo').onchange = e=>{ layerState.echo=e.target.checked;
    eStatuses.forEach(s=>layerState.echoStatus[s]=e.target.checked);
    $('layers').querySelectorAll('[data-echo]').forEach(cb=>cb.checked=e.target.checked); redrawEcho(); };
  $('layers').querySelectorAll('[data-echo]').forEach(cb=> cb.onchange=e=>{
    layerState.echoStatus[e.target.dataset.echo]=e.target.checked;
    layerState.echo=eStatuses.some(s=>layerState.echoStatus[s]);   // master = any status on
    redrawEcho(); syncEcho(eStatuses); });
  if($('L-dep')) $('L-dep').onchange = e=>{ layerState.dep=e.target.checked;
    dStages.forEach(s=>layerState.depStage[s]=e.target.checked);
    $('layers').querySelectorAll('[data-dep]').forEach(cb=>cb.checked=e.target.checked); redrawDep(); };
  $('layers').querySelectorAll('[data-dep]').forEach(cb=> cb.onchange=e=>{
    layerState.depStage[e.target.dataset.dep]=e.target.checked;
    layerState.dep=dStages.some(s=>layerState.depStage[s]);
    redrawDep(); syncDep(dStages); });
  if($('L-mine')) $('L-mine').onchange = e=>{ layerState.mine=e.target.checked;
    mStages.forEach(s=>layerState.mineStage[s]=e.target.checked);
    $('layers').querySelectorAll('[data-mine]').forEach(cb=>cb.checked=e.target.checked); redrawMines(); };
  $('layers').querySelectorAll('[data-mine]').forEach(cb=> cb.onchange=e=>{
    layerState.mineStage[e.target.dataset.mine]=e.target.checked;
    layerState.mine=mStages.some(s=>layerState.mineStage[s]);
    redrawMines(); syncMine(mStages); });
  if($('L-wells')) $('L-wells').onchange = e=>{ layerState.wells=e.target.checked;
    if(e.target.checked){ layerState.wellOrphan=true; layerState.wellOperator=true; loadAbandonedWells(); }
    else redrawWells(); };
  $('layers').querySelectorAll('[data-well]').forEach(cb=> cb.onchange=e=>{
    layerState[e.target.dataset.well==='orphan'?'wellOrphan':'wellOperator']=e.target.checked; redrawWells(); });
  if($('L-wellnear')) $('L-wellnear').onchange = e=>{ layerState.wellNearOnly=e.target.checked; redrawWells(); };
  $('layers').querySelectorAll('[data-star]').forEach(el=> el.addEventListener('click', e=>{ e.stopPropagation(); toggleFollow(el.dataset.star); }));
  $('layers').querySelectorAll('.srow').forEach(rowEl=> rowEl.addEventListener('click', ()=> toggleChart(rowEl.dataset.sid)));
  syncParents(regions,cats);
}
function syncParents(regions,cats){
  const cOn=regions.filter(r=>layerState.regions[r]).length, sOn=cats.filter(c=>layerState.cats[c]).length;
  const cbC=$('L-community'), cbS=$('L-sources');
  if(cbC){ cbC.checked=cOn>0; cbC.indeterminate=cOn>0 && cOn<regions.length; }
  if(cbS){ cbS.checked=sOn>0; cbS.indeterminate=sOn>0 && sOn<cats.length; }
}
function syncEcho(eStatuses){
  const on=eStatuses.filter(s=>layerState.echoStatus[s]).length, cb=$('L-echo');
  if(cb){ cb.checked=on>0; cb.indeterminate=on>0 && on<eStatuses.length; }
}
function syncDep(dStages){
  const on=dStages.filter(s=>layerState.depStage[s]).length, cb=$('L-dep');
  if(cb){ cb.checked=on>0; cb.indeterminate=on>0 && on<dStages.length; }
}
function syncMine(mStages){
  const on=mStages.filter(s=>layerState.mineStage[s]).length, cb=$('L-mine');
  if(cb){ cb.checked=on>0; cb.indeterminate=on>0 && on<mStages.length; }
}

async function loadValidation(){
  const d = await j('/api/validate?correct=' + ($('epacorrect').checked ? 'true' : 'false'));
  const tb = document.querySelector('#validate tbody');
  if (!d.results.length){
    tb.innerHTML = '<tr><td colspan="6" style="color:#888">No reference data yet — '+
      'run <code>ingest reference</code> then it appears here.</td></tr>';
    $('validatenote').textContent = ''; return;
  }
  tb.innerHTML = d.results.map(v => {
    const r = v.r;
    const rcol = r==null ? '#888' : r>=0.7 ? '#1b9e77' : r>=0.4 ? '#e07b00' : '#d62728';
    const rtxt = r==null ? 'n/a' : r.toFixed(2);
    const bad = Math.abs(v.bias) > 50;   // an absurd bias flags a malfunctioning sensor
    const btxt = (v.bias>0?'+':'') + v.bias.toFixed(1) + ' µg/m³';
    return `<tr>
      <td>${v.sensor_name}</td>
      <td>monitor #${v.monitor}</td>
      <td>${v.distance_km} km</td>
      <td>${v.days}</td>
      <td style="color:${rcol};font-weight:600">${rtxt}</td>
      <td style="color:${bad?'#d62728':'#444'};font-weight:${bad?'600':'400'}">${btxt}${bad?' ⚠ malfunction?':''}</td>
    </tr>`;
  }).join('');
  $('validatenote').textContent = d.note;
}

const selected = () => [...chartSet];
function range(){ const s=$('start').value, e=$('end').value;
  return (s?`&start=${s}`:'') + (e?`&end=${e}`:''); }

const nameOf = id => (allSensors.find(s=>s.sensor_id===id)||{}).name || id;
async function render(){
  const ids = selected(), field = $('field').value, rng = range();
  $('dl').href = ids.length ? `/api/export/${ids[0]}.csv` : '#';
  if (!ids.length){
    Plotly.newPlot('ts', [], {margin:{t:10,r:10,b:40,l:45},
      annotations:[{text:'Click a sensor (map or list) to chart it', showarrow:false, font:{color:'#999'}}]},
      {responsive:true, displayModeBar:false});
    Plotly.newPlot('diurnal', [], {margin:{t:10,r:10,b:40,l:45}}, {responsive:true, displayModeBar:false});
    $('cmp').querySelector('tbody').innerHTML = '';
    $('trendinfo').textContent = '';
    return;
  }
  busy('b-ts', true); busy('b-di', true);
  const tsTraces = [], diTraces = [];
  const series = await Promise.all(ids.map(id => j(`/api/series/${id}?field=${field}${rng}`)));
  series.forEach((s,i) => tsTraces.push({x:s.points.map(p=>p.ts), y:s.points.map(p=>p.value),
    mode:'lines', name:s.name, line:{color:COLORS[i%COLORS.length], width:1.3}}));
  if (ids.length === 1){
    const [ev, tr] = await Promise.all([
      j(`/api/events/${ids[0]}?field=${field}${rng}`),
      j(`/api/trend/${ids[0]}?field=${field}${rng}`),
    ]);
    if (ev.events.length) tsTraces.push({x:ev.events.map(e=>e.ts), y:ev.events.map(e=>e.value),
      mode:'markers', name:'event', marker:{color:'#e00', symbol:'x', size:9},
      customdata: ev.events.map(e=>[e.residual, e.score])});
    const pts = series[0].points;
    if (tr.first != null && pts.length){
      tsTraces.push({x:[pts[0].ts, pts[pts.length-1].ts], y:[tr.first, tr.last],
        mode:'lines', name:'trend', line:{dash:'dash', color:'#111', width:2}});
    }
    $('trendinfo').textContent = tr.direction === 'insufficient' ? '' :
      `trend: ${tr.direction}${tr.watch ? ' ⚠ watch' : ''} · Δ${tr.pct_change}% over period (r=${tr.r})`;
  } else { $('trendinfo').textContent = ''; }
  const yMax = Math.max(1, ...tsTraces.flatMap(t => t.y.filter(v => v != null)));
  const gd = await Plotly.newPlot('ts', tsTraces, {margin:{t:10,r:10,b:40,l:45},
    yaxis:{title:field}, legend:{orientation:'h'}, shapes:bandShapes(field, yMax)},
    {responsive:true, displayModeBar:false});
  gd.on('plotly_click', e => {
    const p = e.points[0];
    let msg = `${p.x} — ${field} = ${p.y}`;
    if (p.data.name === 'event' && p.customdata)
      msg += `  ·  event: +${p.customdata[0]} over baseline (z=${p.customdata[1]})`;
    $('detail').textContent = msg;
  });
  gd.on('plotly_legendclick', e => {           // click a legend name to remove that sensor
    const nm = e.data[e.curveNumber].name;
    if (nm === 'event' || nm === 'trend') return true;
    const sid = [...chartSet].find(id => nameOf(id) === nm);
    if (sid) { toggleChart(sid); return false; }
    return true;
  });

  busy('b-ts', false);
  const dis = await Promise.all(ids.map(id => j(`/api/diurnal/${id}?field=${field}${rng}`)));
  dis.forEach((d,i) => diTraces.push({x:d.hours.map(h=>h.hour), y:d.hours.map(h=>h.median),
    mode:'lines+markers', name:d.name, line:{color:COLORS[i%COLORS.length]}}));
  Plotly.newPlot('diurnal', diTraces, {margin:{t:10,r:10,b:40,l:45},
    xaxis:{title:'hour of day (ET)', dtick:2}, yaxis:{title:field}, legend:{orientation:'h'}},
    {responsive:true, displayModeBar:false});
  busy('b-di', false);

  const cmp = await j(`/api/compare?sensors=${ids.join(',')}&field=${field}${rng}`);
  $('cmp').querySelector('tbody').innerHTML = cmp.sensors.map(s =>
    `<tr><td>${s.name}</td><td>${s.day ?? '—'}</td><td>${s.night ?? '—'}</td>
     <td><b>${s.night_day_ratio ?? '—'}</b></td></tr>`).join('');
}
$('field').addEventListener('change', () => { render(); renderGuide(); });
$('start').addEventListener('change', render);
$('end').addEventListener('change', render);
$('clear').addEventListener('click', () => { $('start').value=''; $('end').value=''; render(); });
// ---- Community reports: 📣 map layer (submit form + feedback live in reporting.js) ----
let reportLayer, allReports = [];
const R_ICON = {air:'💨', water:'💧', soil:'🟤', wildlife:'🐾', violation:'⚠️', other:'📣'};
async function loadReports(){
  try { allReports = (await j('/api/reports')).results || []; } catch(e){ allReports = []; }
  redrawReports();
}
function redrawReports(){
  if (reportLayer) reportLayer.remove();
  reportLayer = L.layerGroup();
  if (layerState.reports !== false) allReports.forEach(r => {
    if (r.lat == null) return;
    L.marker([r.lat, r.lon], {icon:L.divIcon({className:'', html:R_ICON[r.domain]||'📣', iconSize:[22,22], iconAnchor:[11,11]})})
      .bindPopup(`<b>${R_ICON[r.domain]||'📣'} ${r.category || r.domain}</b>`+
        (r.description ? `<br>${r.description}` : '')+
        `<br><small>${r.verified ? '✓ verified' : 'Unverified community report'} · ${(r.created_at||'').slice(0,10)}</small>`+
        `<br><small style="color:#a00">Community-reported — not a finding of fact.</small>`)
      .addTo(reportLayer);
  });
  reportLayer.addTo(map);
}
// 📍 Near me — geolocate, drop a pin, zoom in, and list the nearest community sensors
function nearMe(){
  const note = $('nearme-note');
  if (!navigator.geolocation){ if(note) note.textContent='geolocation not available on this device'; return; }
  if (note) note.textContent = 'locating…';
  navigator.geolocation.getCurrentPosition(p=>{
    const lat=p.coords.latitude, lon=p.coords.longitude;
    if (userMarker) userMarker.remove();
    userMarker = L.marker([lat,lon], {zIndexOffset:1000,
      icon:L.divIcon({className:'', html:'📍', iconSize:[26,26], iconAnchor:[13,26]})})
      .addTo(map).bindPopup('You are here').openPopup();
    map.setView([lat,lon], 11);
    const near = allSensors.filter(s=>s.kind==='community' && s.lat!=null)
      .map(s=>({...s, mi:haversineMi(lat,lon,s.lat,s.lon)})).sort((a,b)=>a.mi-b.mi).slice(0,6);
    if (note) note.innerHTML = near.length
      ? '📍 Nearest to you: ' + near.map(s=>`<a href="#" data-nsid="${s.sensor_id}">${s.name}</a> <span class="cnt">${s.mi.toFixed(1)}mi</span>`).join(' · ')
      : 'No community sensors located.';
    note && note.querySelectorAll('[data-nsid]').forEach(a=>a.addEventListener('click', e=>{
      e.preventDefault(); toggleChart(a.dataset.nsid); redrawSensors(); render(); }));
  }, ()=>{ if(note) note.textContent='could not get your location'; }, {enableHighAccuracy:true, timeout:10000});
}
// report/feedback modal logic lives in reporting.js (shared with the Overview page)
// map layer visibility is driven by the layers tree (buildLayers)
loadGuide().then(loadSensors);
loadValidation();
loadCoverage();
initProximity();
const _nm = $('nearme'); if (_nm) _nm.addEventListener('click', nearMe);
$('epacorrect').addEventListener('change', loadValidation);
