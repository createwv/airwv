// Explore v1 (query builder, docs/DIRECTION.md §5.2): a form over /api/query. The form lives in
// the URL (?show=&place=&km=&medium=&param=&since=&limit=&kind=a,b), so a shared link reproduces
// the table; the CSV and API links are the same query with format=csv / as JSON.
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
const FIELDS = ['show', 'km', 'medium', 'param', 'since', 'limit'];

function readForm() {
  const q = new URLSearchParams(location.search);
  const sel = $('ex-place');
  sel.innerHTML = PLACES.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('');
  const lat = parseFloat(q.get('lat')), lon = parseFloat(q.get('lon'));
  if (isFinite(lat) && isFinite(lon) && !q.get('place')) {
    sel.insertAdjacentHTML('afterbegin', `<option value="custom">${esc(q.get('name') || 'Your location')}</option>`);
    sel.options[0].dataset.lat = lat; sel.options[0].dataset.lon = lon;
  }
  sel.value = q.get('place') && PLACES.some(p => p.id === q.get('place')) ? q.get('place') : sel.options[0].value;
  FIELDS.forEach(f => {
    if (!q.has(f)) return;
    const el = form.elements[f];
    if (el.tagName === 'SELECT' && ![...el.options].some(o => o.value === q.get(f))) return;
    el.value = q.get(f);
  });
  const kinds = (q.get('kind') || '').split(',');
  form.querySelectorAll('input[name=kind]').forEach(cb => { cb.checked = kinds.includes(cb.value); });
}

// The form as /api/query parameters (and as this page's own query string).
function params() {
  const opt = $('ex-place').selectedOptions[0];
  const p = PLACES.find(x => x.id === opt.value);
  const lat = p ? p.lat : parseFloat(opt.dataset.lat), lon = p ? p.lon : parseFloat(opt.dataset.lon);
  const api = new URLSearchParams({lat, lon});
  const page = new URLSearchParams(p ? {place: p.id} : {lat, lon, name: opt.textContent});
  FIELDS.forEach(f => {
    const v = form.elements[f].value;
    if (v) { api.set(f, v); page.set(f, v); }
  });
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

async function run() {
  const {api, page} = params();
  history.replaceState(null, '', `${location.pathname}?${page}`);
  const url = `/api/query?${api}`;
  const csv = new URLSearchParams(api); csv.set('format', 'csv');
  $('ex-api').href = url;
  $('ex-csv').href = `/api/query?${csv}`;
  $('ex-count').textContent = 'Loading…';
  try {
    const r = await fetch(url).then(x => x.ok ? x.json() : Promise.reject(x.status));
    const miles = Math.round(r.place.km * 0.621371);
    if (api.get('show') === 'records') {
      const rows = recordRows(r);
      $('ex-count').textContent = `${rows.length} record${rows.length === 1 ? '' : 's'} at ${r.count} place${r.count === 1 ? '' : 's'} within ${miles} mi`
        + (r.filters.since ? `, dated since ${r.filters.since}` : '');
      $('ex-table').innerHTML = rows.length ? recordTable(rows) : '<tbody><tr><td class="meta">No records match.</td></tr></tbody>';
    } else {
      $('ex-count').textContent = `${r.count} place${r.count === 1 ? '' : 's'} within ${miles} mi`
        + (r.count >= r.filters.limit ? ` (first ${r.filters.limit}, nearest first)` : ', nearest first');
      $('ex-table').innerHTML = r.count ? siteTable(r) : '<tbody><tr><td class="meta">Nothing matches.</td></tr></tbody>';
    }
    $('ex-prov').innerHTML = provLine(r.provenance);
  } catch (e) {
    $('ex-count').textContent = `Couldn't load results (${e}).`;
    $('ex-table').innerHTML = '';
  }
}

form.addEventListener('submit', e => { e.preventDefault(); run(); });
readForm();
run();
