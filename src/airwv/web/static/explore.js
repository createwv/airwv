// Explore (query builder, docs/DIRECTION.md §5.2): a form over /api/query. The form lives in
// the URL (?show=&place=&km=&county=|area=&medium=&param=&over=&from=&to=&since=&limit=&kind=a,b), so a shared
// link reproduces the table; the CSV and API links are the same query with format=csv / as JSON.
// The Area picker sends a county as county= and a region as area=; with place=anywhere the
// query has no point, so it covers the whole area. from/to bound which reading counts as latest.
// show=measurements (+ site_id=&from=&to=) lists one site's readings from the series mode.
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

// Known places, as on /place (place.js); they move to the shared state.js when it lands.
const PLACES = [
  {id: 'rutledge', name: 'Rutledge (Charleston)', lat: 38.383, lon: -81.585},
  {id: 'kanawha', name: 'Charleston / Kanawha Valley', lat: 38.3498, lon: -81.6326},
  {id: 'parkersburg', name: 'Parkersburg', lat: 39.2667, lon: -81.5615},
  {id: 'fairmont', name: 'Fairmont', lat: 39.4851, lon: -80.1426},
  {id: 'wyoming', name: 'Wyoming County', lat: 37.5829, lon: -81.5371},
];
const KIND_LABEL = {reference_monitor: 'monitor', sample_site: 'sample site', spill_report: 'spill report',
                    water_system: 'water system', data_center: 'data center'};
const kindLabel = k => KIND_LABEL[k] || k;
const recLabel = k => String(k || '').replace(/_/g, ' ');
const mi = m => m == null ? '—' : `${m < 1 ? m.toFixed(1) : m.toFixed(m < 10 ? 1 : 0)} mi`;
const day = ts => ts ? String(ts).slice(0, 10) : '—';
const link = (url, text) => url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${text}</a>` : text;

const form = $('ex-form');
const FIELDS = ['show', 'km', 'medium', 'param', 'over', 'since', 'site_id', 'from', 'to', 'limit'];
const COUNTY_NOTE = "The county filter only finds places whose data source lists a county. Sensors, gauges and sample sites don't, so they drop out. ";
const REGION_NOTE = 'A region only finds community sensors, grouped by the county their listing gives. ';
const ANYWHERE = {id: 'anywhere', name: 'anywhere in the area'};
const MEASURED_KINDS = 'sensor,reference_monitor,gauge,sample_site';
const isSeries = () => form.elements.show.value === 'measurements';
let urlSite = null;   // a site_id from the URL, until the site list has been filled once

// Show the controls this kind of query uses; hidden ones stay out of the URL (see params).
function syncMode() {
  const series = isSeries();
  form.querySelectorAll('[data-for]').forEach(el => { el.hidden = el.dataset.for !== (series ? 'series' : 'list'); });
  $('ex-param-label').textContent = series ? 'Reading' : 'Latest reading of';
  $('ex-limit-series').disabled = !series;   // /api/query caps site lists at 500 rows
  if (!series && form.elements.limit.value === '5000') form.elements.limit.value = '500';
  $('ex-from-label').textContent = series ? 'From' : 'Readings from';
  $('ex-to-label').textContent = series ? 'To' : 'Readings to';
  // a site's readings need a point to list the sites near it
  const any = $('ex-place').querySelector('option[value=anywhere]');
  any.disabled = series;
  if (series && any.selected) $('ex-place').selectedIndex = 0;
  form.elements.km.disabled = !series && any.selected;
}

// The Area pick as an /api/query parameter: a county is county=, a region area=.
function areaParam() {
  const opt = form.elements.area.selectedOptions[0];
  return opt && opt.value ? [opt.dataset.param, opt.value] : null;
}

function readForm() {
  const q = new URLSearchParams(location.search);
  const sel = $('ex-place');
  sel.innerHTML = [...PLACES, ANYWHERE].map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('');
  const lat = parseFloat(q.get('lat')), lon = parseFloat(q.get('lon'));
  if (isFinite(lat) && isFinite(lon) && !q.get('place')) {
    sel.insertAdjacentHTML('afterbegin', `<option value="custom">${esc(q.get('name') || 'Your location')}</option>`);
    sel.options[0].dataset.lat = lat; sel.options[0].dataset.lon = lon;
  }
  sel.value = q.get('place') && [...PLACES, ANYWHERE].some(p => p.id === q.get('place')) ? q.get('place') : sel.options[0].value;
  const area = form.elements.area;
  const want = q.get('county') ? ['county', q.get('county')] : q.get('area') ? ['area', q.get('area')] : null;
  const opt = want && [...area.options].find(o => o.dataset.param === want[0] && o.value.toLowerCase() === want[1].toLowerCase());
  if (opt) opt.selected = true;
  FIELDS.forEach(f => {
    if (!q.has(f)) return;
    const el = form.elements[f];
    if (el.tagName === 'SELECT' && ![...el.options].some(o => o.value === q.get(f))) return;
    el.value = q.get(f);
  });
  const kinds = (q.get('kind') || '').split(',');
  form.querySelectorAll('input[name=kind]').forEach(cb => { cb.checked = kinds.includes(cb.value); });
  urlSite = q.get('site_id');
  syncMode();
}

// The picked place: its point, and how this page's own query string names it
// (no point for "anywhere in the area").
function where() {
  const opt = $('ex-place').selectedOptions[0];
  if (opt.value === ANYWHERE.id) return {lat: null, lon: null, page: new URLSearchParams({place: ANYWHERE.id})};
  const p = PLACES.find(x => x.id === opt.value);
  const lat = p ? p.lat : parseFloat(opt.dataset.lat), lon = p ? p.lon : parseFloat(opt.dataset.lon);
  return {lat, lon, page: new URLSearchParams(p ? {place: p.id} : {lat, lon, name: opt.textContent})};
}

// The form as /api/query parameters (and as this page's own query string).
function params() {
  const {lat, lon, page} = where();
  const api = new URLSearchParams(lat == null ? {} : {lat, lon});
  FIELDS.filter(f => !form.elements[f].closest('[hidden]') && !form.elements[f].disabled).forEach(f => {
    const v = form.elements[f].value;
    if (f === 'over' && !form.elements.param.value) return;   // /api/query needs a reading to compare
    if (v) { api.set(f, v); page.set(f, v); }
  });
  if (isSeries()) {
    const series = new URLSearchParams({site_id: form.elements.site_id.value, param: form.elements.param.value});
    ['from', 'to', 'over', 'limit'].forEach(f => { if (api.has(f)) series.set(f, api.get(f)); });
    return {api: series, page};
  }
  const area = areaParam();
  if (area) { api.set(...area); page.set(...area); }
  const kinds = [...form.querySelectorAll('input[name=kind]:checked')].map(cb => cb.value);
  if (kinds.length) { api.set('kind', kinds.join(',')); page.set('kind', kinds.join(',')); }
  return {api, page};
}

function siteTable(r) {
  const withParam = !!r.filters.param;
  const head = `<thead><tr><th>Name</th><th>Distance</th>${withParam ? '<th>Latest</th>' : ''}<th class="ex-recs">Records</th></tr></thead>`;
  const rows = r.sites.map(s => {
    const l = s.latest;
    const latest = l ? `${esc(l.value)} ${esc(l.unit || '')}<div class="meta">${esc(l.parameter)} · ${day(l.ts)}</div>` : '<span class="meta">—</span>';
    const recs = s.records.length
      ? s.records.slice(0, 3).map(x => esc(`${recLabel(x.kind)}${x.value != null ? ': ' + x.value : ''}`)).join('<br>')
        + (s.records.length > 3 ? `<div class="meta">+${s.records.length - 3} more</div>` : '')
      : '<span class="meta">—</span>';
    return `<tr><td>${link(s.url, esc(s.name || s.ref))}<div class="meta">${esc(kindLabel(s.kind))} · ${esc(s.medium)}${s.county ? ' · ' + esc(s.county) : ''}</div>${s.records.length ? `<div class="ex-recs-in">${recs}</div>` : ''}</td>
      <td>${mi(s.mi)}</td>${withParam ? `<td>${latest}</td>` : ''}<td class="ex-recs">${recs}</td></tr>`;
  });
  return head + `<tbody>${rows.join('')}</tbody>`;
}

function recordRows(r) {
  if (r.filters.since) return r.changes;
  return r.sites.flatMap(s => s.records.map(x => ({site_id: s.site_id, site_kind: s.kind, name: s.name,
                                                    medium: s.medium, mi: s.mi, ...x})));
}

function recordTable(rows) {
  const head = '<thead><tr><th>Record</th><th>Date</th><th>Where</th><th>Distance</th></tr></thead>';
  const body = rows.map(x => `<tr><td>${link(x.url, esc(recLabel(x.kind)))}${x.value != null ? `<div>${esc(x.value)}</div>` : ''}</td>
    <td>${day(x.effective)}</td><td>${esc(x.name)}<div class="meta">${esc(kindLabel(x.site_kind))}</div></td><td>${mi(x.mi)}</td></tr>`);
  return head + `<tbody>${body.join('')}</tbody>`;
}

function provLine(prov) {
  const parts = Object.entries(prov || {}).map(([layer, p]) =>
    `${esc(p.source || layer)}${p.fetched_at ? ` (fetched ${esc(String(p.fetched_at).slice(0, 10))})` : ''}`);
  return parts.length ? `Data sources: ${parts.join('; ')}. Readings come from the collectors' database. ` +
    '<a href="/data">About these data sources →</a>' : '';
}

// The Site list: measured sites near the place, nearest first, with the picked reading if any.
function fillSites(sites) {
  const sel = form.elements.site_id;
  const want = urlSite || sel.value;
  sel.innerHTML = sites.map(s => `<option value="${esc(s.site_id)}">${esc(`${s.name || s.ref} · ${kindLabel(s.kind)} · ${mi(s.mi)}`)}</option>`).join('')
    || '<option value="">No site with readings here</option>';
  if (urlSite && !sites.some(s => s.site_id === urlSite)) {
    sel.insertAdjacentHTML('afterbegin', `<option value="${esc(urlSite)}">${esc(urlSite)}</option>`);
  }
  urlSite = null;
  if ([...sel.options].some(o => o.value === want)) sel.value = want;
}

let siteKey = null, sitesReady = null;
function loadSites() {
  const {lat, lon} = where();
  const q = new URLSearchParams({lat, lon, km: form.elements.km.value, kind: MEASURED_KINDS, limit: 500});
  const medium = form.elements.medium.value, param = form.elements.param.value;
  if (medium) q.set('medium', medium);
  if (param) q.set('param', param);
  const key = q.toString();
  if (key === siteKey) return sitesReady;
  siteKey = key;
  sitesReady = fetch(`/api/query?${q}`).then(x => x.ok ? x.json() : Promise.reject(x.status)).then(r => {
    if (key === siteKey) fillSites(r.sites.filter(s => !param || s.latest));
  }).catch(e => { if (key === siteKey) siteKey = null; throw e; });
  return sitesReady;
}

function setLinks(api) {
  $('ex-links').hidden = !api;
  if (!api) return;
  const csv = new URLSearchParams(api); csv.set('format', 'csv');
  $('ex-api').href = `/api/query?${api}`;
  $('ex-csv').href = `/api/query?${csv}`;
}

function seriesTable(rows) {
  const head = '<thead><tr><th>Time</th><th>Value</th><th>Data source</th><th>Quality</th></tr></thead>';
  const when = ts => String(ts).length > 10 ? `${String(ts).slice(0, 16).replace('T', ' ')} UTC` : String(ts);
  const body = rows.map(x => `<tr><td>${esc(when(x.ts))}</td><td>${esc(x.value)} ${esc(x.unit || '')}</td>
    <td>${esc(x.source || '—')}</td><td>${esc(x.quality || '—')}</td></tr>`);
  return head + `<tbody>${body.join('')}</tbody>`;
}

function seriesCount(r, label) {
  const {from, to, limit, over} = r.filters;
  const range = from && to ? `, ${from} to ${to}` : from ? `, from ${from}` : to ? `, up to ${to}` : '';
  return `${r.count} reading${r.count === 1 ? '' : 's'} of ${esc(label)}${over != null ? ` over ${esc(over)}` : ''} at ${link(r.site.url, esc(r.site.name || r.site.ref))}${esc(range)}`
    + (r.truncated ? ` (newest ${limit} only)` : '');
}

async function runSeries() {
  $('ex-count').textContent = 'Loading…';
  try {
    await loadSites();
    const {api, page} = params();
    history.replaceState(null, '', `${location.pathname}?${page}`);
    if (!api.get('site_id') || !api.get('param')) {
      setLinks(null);
      $('ex-count').textContent = api.get('site_id') ? 'Pick a reading to list its measurements.'
        : 'No site near here has this reading. Try a wider distance or another reading.';
      $('ex-table').innerHTML = $('ex-prov').innerHTML = '';
      return;
    }
    setLinks(api);
    const r = await fetch(`/api/query?${api}`).then(x => x.ok ? x.json() : Promise.reject(x.status));
    $('ex-count').innerHTML = seriesCount(r, form.elements.param.selectedOptions[0].textContent);
    $('ex-table').innerHTML = r.count ? seriesTable(r.rows) : '<tbody><tr><td class="meta">No readings match.</td></tr></tbody>';
    $('ex-prov').innerHTML = provLine(r.provenance);
  } catch (e) {
    $('ex-count').textContent = `Couldn't load results (${e}).`;
    $('ex-table').innerHTML = '';
  }
}

// How the area, value and reading-window filters read in the result line.
function filterNote(r) {
  const {county, area, over, param, from, to} = r.filters;
  const reading = form.elements.param.selectedOptions[0].textContent;
  const range = from && to ? `${from} to ${to}` : from ? `since ${from}` : to ? `up to ${to}` : '';
  return (county ? ` in ${county.join(', ')} County` : '') + (area ? ` in ${area}` : '')
    + (over != null ? `, latest ${reading} over ${over}` : '')
    + (param && range ? `, ${reading} readings ${range}` : '');
}

// Where the result line says the places are: within N mi of the point, or just the area.
const within = r => r.place ? ` within ${Math.round(r.place.km * 0.621371)} mi` : '';

async function run() {
  if (isSeries()) return runSeries();
  const {api, page} = params();
  history.replaceState(null, '', `${location.pathname}?${page}`);
  const url = `/api/query?${api}`;
  if (!api.has('lat') && !areaParam()) {
    setLinks(null);
    $('ex-count').textContent = 'Pick an area to search anywhere in it, or a place to search near.';
    $('ex-table').innerHTML = $('ex-prov').innerHTML = '';
    return;
  }
  setLinks(api);
  $('ex-count').textContent = 'Loading…';
  try {
    const r = await fetch(url).then(x => x.ok ? x.json() : Promise.reject(x.status));
    if (api.get('show') === 'records') {
      const rows = recordRows(r);
      $('ex-count').textContent = `${rows.length} record${rows.length === 1 ? '' : 's'} at ${r.count} place${r.count === 1 ? '' : 's'}${within(r)}${filterNote(r)}`
        + (r.filters.since ? `, dated since ${r.filters.since}` : '');
      $('ex-table').innerHTML = rows.length ? recordTable(rows) : '<tbody><tr><td class="meta">No records match.</td></tr></tbody>';
    } else {
      const order = r.place ? 'nearest first' : 'by data source';
      $('ex-count').textContent = `${r.count} place${r.count === 1 ? '' : 's'}${within(r)}${filterNote(r)}`
        + (r.count >= r.filters.limit ? ` (first ${r.filters.limit}, ${order})` : `, ${order}`);
      $('ex-table').innerHTML = r.count ? siteTable(r) : '<tbody><tr><td class="meta">Nothing matches.</td></tr></tbody>';
    }
    $('ex-prov').innerHTML = (r.filters.county ? COUNTY_NOTE : '') + (r.filters.area ? REGION_NOTE : '')
      + provLine(r.provenance);
  } catch (e) {
    $('ex-count').textContent = `Couldn't load results (${e}).`;
    $('ex-table').innerHTML = '';
  }
}

form.addEventListener('submit', e => { e.preventDefault(); run(); });
form.addEventListener('change', () => {
  syncMode();
  if (isSeries()) loadSites().catch(() => {});   // run() reports a failed list
});
readForm();
run();
