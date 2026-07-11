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
const layerState = {community:true, reference:true, sources:true, regions:{}, cats:{}};
function drawMap(sensors){
  if (!map){
    map = L.map('map').setView([38.35, -81.6], 8);
    const tile = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
      {maxZoom:18, attribution:'© OpenStreetMap'}).addTo(map);
    tile.on('load', () => busy('b-map', false));      // hide spinner once tiles are in
    setTimeout(() => busy('b-map', false), 5000);     // fallback
  }
  redrawSensors();
  const pts = allSensors.filter(s => s.kind !== 'reference' && s.lat != null).map(s => [s.lat, s.lon]);
  if (pts.length) map.fitBounds(pts, {padding:[30,30], maxZoom:11});
  loadSources();
}
function redrawSensors(){
  if (sensorLayer) sensorLayer.remove();
  if (refLayer) refLayer.remove();
  sensorLayer = L.layerGroup();
  refLayer = L.layerGroup();
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
}
let sensorLayer, refLayer;
// reference monitors are now drawn live in drawMap() (ringed circles, current PM2.5)
const SRC_ICON = {power:'⚡', chemical:'⚗️', oil_gas:'🛢️', materials:'⛏️', waste:'🗑️', other:'🏭'};
const SRC_LABEL = {power:'Power plant', chemical:'Chemical', oil_gas:'Oil & gas',
  materials:'Metals / mining / materials', waste:'Waste', other:'Other TRI facility'};
let sourceLayer, allSources_ = [], srcDisclaimer = '';
async function loadSources(){
  const data = await j('/api/sources');
  allSources_ = data.sources; srcDisclaimer = data.disclaimer || '';
  redrawSources();
  buildLayers();
  $('srclist').innerHTML = allSources_.filter(s=>s.lat!=null)
    .map(s=>`<option value="${s.name.replace(/"/g,'&quot;')}">`).join('');
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
function redrawSources(){
  if (sourceLayer) sourceLayer.remove();
  sourceLayer = L.layerGroup();
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
  // community: each region is a <details> (visibility checkbox) holding clickable sensor rows
  const byRegion = {};
  comm.forEach(s => { (byRegion[s.region] = byRegion[s.region] || []).push(s); });
  const regionBlocks = regions.map(r => {
    const rows = (byRegion[r]||[]).sort((a,b)=>a.name.localeCompare(b.name)).map(s =>
      `<div class="srow${chartSet.has(s.sensor_id)?' on':''}" data-sid="${s.sensor_id}">${s.name}</div>`).join('');
    return `<details><summary><input type="checkbox" data-region="${r}" ${layerState.regions[r]?'checked':''}> ${r} <span class="cnt">${rCounts[r]}</span></summary><div class="children">${rows}</div></details>`;
  }).join('');
  const catRows = cats.map(c=>row('cat',c,layerState.cats[c],`${SRC_ICON[c]} ${SRC_LABEL[c]}`,cCounts[c])).join('');
  $('layers').innerHTML =
    `<b style="font-size:12px;color:#555">Sensors &amp; layers <span class="cnt">(click a sensor to chart it)</span></b>`+
    `<details open><summary><input type="checkbox" id="L-community" ${layerState.community?'checked':''}> ● Community sensors <span class="cnt">${comm.length}</span></summary><div class="children">${regionBlocks}</div></details>`+
    `<label style="align-self:center"><input type="checkbox" id="L-reference" ${layerState.reference?'checked':''}> ◎ Reference monitors <span class="cnt">${ref.length}</span></label>`+
    `<details><summary><input type="checkbox" id="L-sources" ${layerState.sources?'checked':''}> 🏭 Pollution sources <span class="cnt">${allSources_.length}</span></summary><div class="children">${catRows}</div></details>`;
  // checkboxes in a <summary> shouldn't toggle its open/close
  $('layers').querySelectorAll('summary input[type=checkbox]').forEach(cb=> cb.addEventListener('click', e=>e.stopPropagation()));
  // updates happen in place (no rebuild) so open groups stay open
  $('L-community').onchange = e=>{ layerState.community=e.target.checked;
    regions.forEach(r=>layerState.regions[r]=e.target.checked);
    $('layers').querySelectorAll('[data-region]').forEach(cb=>{cb.checked=e.target.checked;cb.indeterminate=false;}); redrawSensors(); };
  $('L-reference').onchange = e=>{ layerState.reference=e.target.checked; redrawSensors(); };
  $('L-sources').onchange = e=>{ layerState.sources=e.target.checked;
    cats.forEach(c=>layerState.cats[c]=e.target.checked);
    $('layers').querySelectorAll('[data-cat]').forEach(cb=>cb.checked=e.target.checked); redrawSources(); };
  $('layers').querySelectorAll('[data-region]').forEach(cb=> cb.onchange=e=>{
    layerState.regions[e.target.dataset.region]=e.target.checked;
    layerState.community=regions.some(r=>layerState.regions[r]); redrawSensors(); syncParents(regions,cats); });
  $('layers').querySelectorAll('[data-cat]').forEach(cb=> cb.onchange=e=>{
    layerState.cats[e.target.dataset.cat]=e.target.checked;
    layerState.sources=cats.some(c=>layerState.cats[c]); redrawSources(); syncParents(regions,cats); });
  $('layers').querySelectorAll('.srow').forEach(rowEl=> rowEl.addEventListener('click', ()=> toggleChart(rowEl.dataset.sid)));
  syncParents(regions,cats);
}
function syncParents(regions,cats){
  const cOn=regions.filter(r=>layerState.regions[r]).length, sOn=cats.filter(c=>layerState.cats[c]).length;
  const cbC=$('L-community'), cbS=$('L-sources');
  if(cbC){ cbC.checked=cOn>0; cbC.indeterminate=cOn>0 && cOn<regions.length; }
  if(cbS){ cbS.checked=sOn>0; cbS.indeterminate=sOn>0 && sOn<cats.length; }
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
// map layer visibility is driven by the layers tree (buildLayers)
loadGuide().then(loadSensors);
loadValidation();
loadCoverage();
initProximity();
$('epacorrect').addEventListener('change', loadValidation);
