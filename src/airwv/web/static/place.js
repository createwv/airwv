// "My place now" (basic view, docs/DIRECTION.md §2): one status line per medium and
// "what changed (7 days)" for a place. Place lives in the URL (?lat=&lon=&km=&place=);
// every line comes from /api/query, so each number keeps its time, distance and source.
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

// Known places (DIRECTION §4); Rutledge is the demo place and the default. Approximate centres.
const PLACES = [
  {id: 'rutledge', name: 'Rutledge (Charleston)', lat: 38.383, lon: -81.585},
  {id: 'kanawha', name: 'Charleston / Kanawha Valley', lat: 38.3498, lon: -81.6326},
  {id: 'parkersburg', name: 'Parkersburg', lat: 39.2667, lon: -81.5615},
  {id: 'fairmont', name: 'Fairmont', lat: 39.4851, lon: -80.1426},
  {id: 'wyoming', name: 'Wyoming County', lat: 37.5829, lon: -81.5371},
];
// EPA PM2.5 AQI categories (mirror of PM25_BANDS in app.py)
const PM_BANDS = [
  {max: 12.0, label: 'good', color: '#00e400'},
  {max: 35.4, label: 'moderate', color: '#ffff00'},
  {max: 55.4, label: 'unhealthy for sensitive groups', color: '#ff7e00'},
  {max: 150.4, label: 'unhealthy', color: '#ff0000'},
  {max: 250.4, label: 'very unhealthy', color: '#8f3f97'},
  {max: Infinity, label: 'hazardous', color: '#7e0023'},
];
const bandIx = v => PM_BANDS.findIndex(b => v <= b.max);
const WATER_PARAMS = ['conductance', 'ph', 'do', 'turbidity', 'temperature', 'iron', 'selenium',
                      'manganese', 'sulfate', 'ecoli'];
const WATER_LABEL = {conductance: 'conductivity', ph: 'pH', do: 'dissolved oxygen', ecoli: 'E. coli'};
const LIVE_HOURS = 6, CHANGE_DAYS = 7;

const toDate = ts => ts ? new Date(String(ts).replace(' ', 'T').slice(0, 19) + (String(ts).length > 10 ? 'Z' : '')) : null;
const fmtTime = ts => { const d = toDate(ts); return d ? d.toLocaleString('en-US',
  {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}) : '—'; };
const fmtDay = ts => { const d = toDate(ts); return d ? d.toLocaleDateString('en-US',   // bare dates are UTC
  {month: 'short', day: 'numeric', year: 'numeric', timeZone: String(ts).length > 10 ? undefined : 'UTC'}) : '—'; };
const mi = m => `${m < 1 ? m.toFixed(1) : Math.round(m)} mi`;
const miles = km => Math.round(km * 0.621371);
const isLive = ts => { const d = toDate(ts); return d && (Date.now() - d) < LIVE_HOURS * 3600e3; };

function readPlace() {
  const q = new URLSearchParams(location.search);
  const known = PLACES.find(p => p.id === q.get('place')) || PLACES[0];
  const lat = parseFloat(q.get('lat')), lon = parseFloat(q.get('lon'));
  const custom = isFinite(lat) && isFinite(lon) && !(q.get('place') && known.id === q.get('place'));
  return {id: custom ? '' : known.id, name: custom ? (q.get('name') || 'Your location') : known.name,
          lat: custom ? lat : known.lat, lon: custom ? lon : known.lon,
          km: parseFloat(q.get('km')) || 8};
}

function writePlace(p) {
  const q = new URLSearchParams(location.search);
  ['place', 'name', 'lat', 'lon'].forEach(k => q.delete(k));
  if (p.id) q.set('place', p.id);
  else { q.set('lat', p.lat.toFixed(4)); q.set('lon', p.lon.toFixed(4)); q.set('name', p.name); }
  q.set('km', p.km);
  history.replaceState(null, '', `${location.pathname}?${q}`);
}

const api = (p, extra) => fetch(`/api/query?lat=${p.lat}&lon=${p.lon}&km=${p.km}&${extra}`)
  .then(r => r.ok ? r.json() : Promise.reject(r.status));

const line = (medium, color, html, more) => `<div class="pl-line">
  <span class="pl-dot" style="background:${color || '#cfd6dc'}"></span>
  <div class="pl-body"><b>${medium}:</b> ${html}${more ? ` <span class="pl-more">${more}</span>` : ''}</div></div>`;

function airLine(air, p) {
  const withVal = air.sites.filter(s => s.latest);
  const live = withVal.filter(s => isLive(s.latest.ts));
  const s = live[0] || withVal[0];
  if (!s) return line('Air', null, `no sensor or monitor with readings within ${miles(p.km)} mi.`,
                      '<a href="/air">statewide map →</a>');
  const v = s.latest.value, b = PM_BANDS[bandIx(v)];
  const who = s.kind === 'reference_monitor' ? 'nearest EPA monitor' : 'nearest sensor';
  const when = live.length ? `as of ${fmtTime(s.latest.ts)}` : `no live reading; last one ${fmtTime(s.latest.ts)}`;
  return line('Air', live.length ? b.color : null,
    `${b.label}, ${v.toFixed(0)} µg/m³ PM2.5, ${when}, ${who} ${esc(s.name)} ${mi(s.mi)}`,
    '<a href="/air">data behind this →</a>');
}

function waterLine(water, p) {
  const withVal = water.sites.filter(s => s.latest);
  const gauge = withVal.find(s => s.kind === 'gauge' && isLive(s.latest.ts));
  const fmt = l => `${WATER_LABEL[l.parameter] || l.parameter} ${+l.value.toFixed(2)} ${esc(l.unit || '')}`.trim();
  let html;
  if (gauge) {
    html = `live gauge ${esc(gauge.name)} ${mi(gauge.mi)}: ${fmt(gauge.latest)}, as of ${fmtTime(gauge.latest.ts)}`;
  } else {
    const sample = withVal[0];
    html = `no live gauge within ${miles(p.km)} mi`
      + (sample ? `; nearest reading ${esc(sample.name)} ${mi(sample.mi)}: ${fmt(sample.latest)}, ${fmtDay(sample.latest.ts)}`
                : '; no water samples on file nearby');
  }
  return line('Water', gauge ? '#2c7fb8' : null, html + '.', '<a href="/water">data behind this →</a>');
}

function drinkingLine(systems) {
  const bad = systems.sites.filter(s => s.records.some(r => r.kind === 'health_violation'));
  if (!systems.sites.length) return '';
  if (!bad.length) return line('Drinking water', null,
    `${systems.sites.length} public water system${systems.sites.length > 1 ? 's' : ''} nearby; none listed with a health-based violation.`);
  const s = bad[0];
  return line('Drinking water', '#ff7e00',
    `${bad.length} of ${systems.sites.length} nearby public water systems listed with a health-based violation`
    + ` (EPA SDWIS), e.g. <a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a>.`);
}

function landLine(wells, p) {
  const n = wells.sites.length, orphan = wells.sites.filter(s => s.records.some(r => r.kind === 'orphan')).length;
  const count = n >= wells.filters.limit ? `${n}+` : `${n}`;
  return line('Land', null,
    (n ? `${count} abandoned gas wells within ${miles(p.km)} mi` + (orphan ? ` (${orphan} orphan)` : '')
       + `, nearest ${mi(wells.sites[0].mi)}.` : `no abandoned gas wells on file within ${miles(p.km)} mi.`)
    + ' No cleanup-site data yet.', '<a href="/nearby">see them →</a>');
}

function changeItems(air, recs) {
  const out = [];
  air.sites.filter(s => s.peak && bandIx(s.peak.value) > 0)
    .sort((a, b) => b.peak.value - a.peak.value).slice(0, 3)
    .forEach(s => out.push({when: s.peak.ts, color: PM_BANDS[bandIx(s.peak.value)].color,
      html: `Air reached <b>${PM_BANDS[bandIx(s.peak.value)].label}</b> (${s.peak.value.toFixed(0)} µg/m³ PM2.5)`
        + ` at ${esc(s.name)}, ${mi(s.mi)}`}));
  recs.changes.slice(0, 8).forEach(c => {
    const link = c.url ? ` <a href="${esc(c.url)}" target="_blank" rel="noopener">record →</a>` : '';
    if (c.kind === 'spill') out.push({when: c.effective, color: '#8e44ad',
      html: `Spill reported to the NRC: ${esc(c.value || 'release')}, ${mi(c.mi)}${link}`});
    else if (c.kind === 'permit_stage') out.push({when: c.effective, color: null,
      html: `Permit dated: ${esc(c.name || c.site_id)} (${esc(c.value)}), ${mi(c.mi)}${link}`});
    else out.push({when: c.effective, color: null, html: `${esc(c.kind.replace('_', ' '))}: ${esc(c.name)}, ${mi(c.mi)}${link}`});
  });
  return out.sort((a, b) => (toDate(b.when) || 0) - (toDate(a.when) || 0));   // newest first
}

function provLine(results) {
  const prov = Object.assign({}, ...results.map(r => r.provenance));
  const parts = Object.entries(prov).filter(([, v]) => v.fetched_at)
    .map(([k, v]) => `${esc(v.source || k)} (updated ${fmtDay(v.fetched_at)})`);
  return (parts.length ? `Data: ${parts.join(' · ')}. ` : '')
    + 'Sensor and gauge times are shown on each line. <a href="/data">All data sources →</a>';
}

async function load(p) {
  writePlace(p);
  $('pl-title').textContent = `${p.name}, now`;
  $('pl-km').value = String(p.km);
  $('pl-place').value = p.id;
  $('pl-nearby').href = `/nearby?lat=${p.lat}&lon=${p.lon}&km=${p.km}`;
  $('pl-watch').href = `/alerts?lat=${p.lat}&lon=${p.lon}`;
  const since = new Date(Date.now() - CHANGE_DAYS * 864e5).toISOString().slice(0, 10);
  $('pl-since').textContent = `since ${fmtDay(since)}`;
  try {
    const [air, water, systems, wells, recs] = await Promise.all([
      api(p, `kind=sensor,reference_monitor&param=pm2_5&since=${since}&limit=50`),
      api(p, `kind=gauge,sample_site&param=${WATER_PARAMS.join(',')}&limit=50`),
      api(p, 'kind=water_system&limit=100'),
      api(p, 'kind=well&limit=500'),
      api(p, `kind=spill_report,permit,facility&since=${since}&limit=500`),
    ]);
    $('pl-status').innerHTML = airLine(air, p) + waterLine(water, p) + drinkingLine(systems) + landLine(wells, p);
    const items = changeItems(air, recs);
    $('pl-changes').innerHTML = items.length
      ? items.map(i => line(fmtDay(i.when), i.color, i.html)).join('')
      : `<div class="pl-line meta">Nothing new in our data within ${miles(p.km)} mi in the last ${CHANGE_DAYS} days:`
        + ' no air readings above good, no new spill reports, no newly dated permits.'
        + ' Check when each data source was last updated below.</div>';
    $('pl-prov').innerHTML = provLine([air, water, systems, wells, recs]);
  } catch (e) {
    $('pl-status').innerHTML = '<div class="pl-line meta">Could not load this place. Try again in a moment.</div>';
    $('pl-changes').innerHTML = '';
  }
}

function init() {
  let p = readPlace();
  $('pl-place').innerHTML = PLACES.map(x => `<option value="${x.id}">${esc(x.name)}</option>`).join('')
    + '<option value="">Your location</option>';
  $('pl-place').addEventListener('change', () => {
    const k = PLACES.find(x => x.id === $('pl-place').value);
    if (k) { p = {...k, km: p.km}; load(p); } else $('pl-loc').click();
  });
  $('pl-km').addEventListener('change', () => { p = {...p, km: parseFloat($('pl-km').value)}; load(p); });
  $('pl-loc').addEventListener('click', () => {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(pos => {
      p = {id: '', name: 'Your location', lat: pos.coords.latitude, lon: pos.coords.longitude, km: p.km};
      load(p);
    }, () => { $('pl-place').value = p.id; });
  });
  $('pl-report').addEventListener('click', e => {
    e.preventDefault();
    if (window.AIRWV_REPORT_AT) window.AIRWV_REPORT_AT({lat: p.lat, lon: p.lon, domain: 'other',
      description: `Reporting something near ${p.name}. What did you notice? `});
  });
  load(p);
}
init();
