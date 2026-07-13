// Sources & Facilities browse page. Carousel of featured facilities + a filterable
// grid; click any card for a detail view with a Street View photo (when a Google Maps
// key is configured), the facts we cite, and the nearest air sensors. No map on this
// page — the "nearby sensors" list is computed from /api/sensors client-side.
const $ = id => document.getElementById(id);
const esc = s => (s || '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const SV_KEY = (window.SV_KEY || '').trim();

const CAT = {
  power:    {label: 'Power plant',        icon: '⚡', color: '#c0392b'},
  chemical: {label: 'Chemical',           icon: '⚗️', color: '#8e44ad'},
  oil_gas:  {label: 'Oil & gas',          icon: '🛢️', color: '#7a5b12'},
  materials:{label: 'Metals / materials', icon: '🏗️', color: '#5a6472'},
  waste:    {label: 'Waste',              icon: '♻️', color: '#2a7a2a'},
  water_discharge: {label: 'Water discharge (NPDES)', icon: '💧', color: '#2c9fd6'},
  other:    {label: 'TRI facility',       icon: '🏭', color: '#4a7fb0'},
};
const CAT_ORDER = ['power', 'chemical', 'oil_gas', 'water_discharge', 'waste', 'materials', 'other'];

let SOURCES = [], SENSORS = [], DISCLAIMER = '';
let FACILITIES = [], FAC_META = {};

// EPA ECHO compliance status → badge; program code → chip.
const FAC_STATUS = {
  significant_violation: {label: 'Significant violation', color: '#c0392b', icon: '⛔'},
  violation:            {label: 'In violation',          color: '#e07b00', icon: '⚠️'},
  compliant:            {label: 'No violation',          color: '#137333', icon: '✓'},
  unknown:              {label: 'Not tracked',           color: '#8a94a0', icon: '·'},
};
const PROG = {air: '🌫️ Air', water: '💧 Water', waste: '♻️ Waste',
  'drinking-water': '🚰 Drinking water', 'toxics-release': '☢️ Toxics'};

// ---- geo helpers (mirror of app.js proximity math) ----
const DIRS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
function haversineMi(la1, lo1, la2, lo2) {
  const R = 6371, rad = x => x * Math.PI / 180;
  const dp = rad(la2 - la1), dl = rad(lo2 - lo1);
  const h = Math.sin(dp / 2) ** 2 + Math.cos(rad(la1)) * Math.cos(rad(la2)) * Math.sin(dl / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h)) * 0.621371;
}
function bearing8(la1, lo1, la2, lo2) {
  const rad = x => x * Math.PI / 180;
  const y = Math.sin(rad(lo2 - lo1)) * Math.cos(rad(la2));
  const x = Math.cos(rad(la1)) * Math.sin(rad(la2)) - Math.sin(rad(la1)) * Math.cos(rad(la2)) * Math.cos(rad(lo2 - lo1));
  return DIRS[Math.round(((Math.atan2(y, x) * 180 / Math.PI + 360) % 360) / 45) % 8];
}

function svUrl(lat, lon, w, h) {
  if (!SV_KEY) return null;
  return `https://maps.googleapis.com/maps/api/streetview?size=${w}x${h}`
    + `&location=${lat},${lon}&fov=78&source=outdoor&return_error_code=true&key=${SV_KEY}`;
}
// image element that falls back to a category placeholder if there's no key / no pano
function phStyle(cat) {
  const c = CAT[cat] || CAT.other;
  return `background:linear-gradient(135deg,${c.color}22,${c.color}44)`;
}
function phInner(icon) {
  return `<span class="ph-ic">${icon}</span><span class="ph-cap">📷 photo coming soon</span>`;
}
function thumb(s, w, h, cls) {
  const c = CAT[s.category] || CAT.other;
  const url = svUrl(s.lat, s.lon, w, h);
  if (!url) return `<div class="${cls} ph" style="${phStyle(s.category)}">${phInner(c.icon)}</div>`;
  return `<img class="${cls}" loading="lazy" alt="${esc(s.name)}" src="${url}"`
    + ` data-cls="${cls}" data-icon="${c.icon}" data-style="${phStyle(s.category)}" onerror="svFail(this)">`;
}
// Street View returned no panorama (or no key) → swap the <img> for a category tile
window.svFail = function (img) {
  const d = document.createElement('div');
  d.className = img.dataset.cls + ' ph';
  d.style.cssText = img.dataset.style;
  d.innerHTML = phInner(img.dataset.icon);
  img.replaceWith(d);
};

function card(s, i) {
  const c = CAT[s.category] || CAT.other;
  return `<button class="srccard" data-i="${i}">
    ${thumb(s, 320, 150, 'srccard-img')}
    <div class="srccard-body">
      <div class="srccard-name">${esc(s.name)}</div>
      <div class="srccard-meta"><span class="catpill" style="background:${c.color}">${c.icon} ${c.label}</span></div>
      <div class="srccard-op">${esc(s.operator || s.type || '')}</div>
    </div></button>`;
}

function renderCarousel() {
  const featured = SOURCES
    .filter(s => s.category && s.category !== 'other' && s.lat != null)
    .sort((a, b) => CAT_ORDER.indexOf(a.category) - CAT_ORDER.indexOf(b.category))
    .slice(0, 40);
  $('carousel').innerHTML = featured.map(s => card(s, SOURCES.indexOf(s))).join('')
    || '<p class="meta" style="padding:14px">No featured facilities.</p>';
}

function renderGrid() {
  const q = $('src-q').value.trim().toLowerCase();
  const cat = $('src-cat').value;
  const rows = SOURCES.filter(s => s.lat != null
    && (!cat || s.category === cat)
    && (!q || (s.name + ' ' + (s.operator || '') + ' ' + (s.type || '')).toLowerCase().includes(q)));
  $('src-count').textContent = `${rows.length} facilit${rows.length === 1 ? 'y' : 'ies'}`;
  $('src-grid').innerHTML = rows.map(s => card(s, SOURCES.indexOf(s))).join('')
    || '<p class="meta" style="padding:14px">No matches.</p>';
}

function renderFacilities() {
  const st = $('fac-status').value, pg = $('fac-program').value;
  const rows = FACILITIES.filter(f => (!st || f.status === st) && (!pg || (f.programs || []).includes(pg)));
  $('fac-count').textContent = `${rows.length} of ${FACILITIES.length} facilities`;
  $('fac-body').innerHTML = rows.map(f => {
    const s = FAC_STATUS[f.status] || FAC_STATUS.unknown;
    const chips = (f.programs || []).map(p => `<span class="prog-chip">${PROG[p] || p}</span>`).join(' ');
    const loc = [f.city, f.county && `${f.county} Co.`].filter(Boolean).join(', ');
    return `<tr>
      <td><b>${esc(f.name)}</b>${f.last_inspection ? `<div class="meta">last inspected ${esc(f.last_inspection)}</div>` : ''}</td>
      <td class="meta">${esc(loc)}</td>
      <td>${chips || '<span class="meta">—</span>'}</td>
      <td><span class="fac-badge" style="background:${s.color}">${s.icon} ${s.label}</span></td>
      <td><a href="${esc(f.echo_url)}" target="_blank" rel="noopener">ECHO report →</a></td>
    </tr>`;
  }).join('') || '<tr><td colspan="5" class="meta" style="padding:14px">No facilities match.</td></tr>';
}

function renderFacSummary() {
  const s = FAC_META.summary || {};
  const box = (n, txt, color) => `<span class="fac-stat" style="border-color:${color}">
    <b style="color:${color}">${n ?? '—'}</b> ${txt}</span>`;
  $('fac-summary').innerHTML =
    box(s.total, 'major facilities', '#5a6472')
    + box(s.significant_violation, 'in significant violation', '#c0392b')
    + box(s.violation, 'in violation', '#e07b00')
    + box(s.compliant, 'no violation identified', '#137333');
  $('fac-src').textContent = FAC_META.fetched_at ? `· EPA ECHO, as of ${FAC_META.fetched_at}` : '';
  $('fac-disc').textContent = FAC_META.disclaimer || '';
}

function nearbySensors(s) {
  return SENSORS.filter(x => x.lat != null)
    .map(x => ({...x, mi: haversineMi(s.lat, s.lon, x.lat, x.lon), dir: bearing8(s.lat, s.lon, x.lat, x.lon)}))
    .sort((a, b) => a.mi - b.mi).slice(0, 6);
}

function openDetail(s) {
  const c = CAT[s.category] || CAT.other;
  $('sd-name').textContent = s.name;
  $('sd-cat').textContent = `${c.icon} ${c.label}`;
  $('sd-cat').style.background = c.color;
  $('sd-type').textContent = s.type || '—';
  $('sd-operator').textContent = s.operator || 'Unknown';
  $('sd-citation').textContent = s.citation || 'public record';
  $('sd-imgwrap').innerHTML = thumb(s, 520, 240, 'sd-img');
  const near = nearbySensors(s);
  $('sd-sensors').innerHTML = near.length ? near.map(x => `<tr>
    <td>${esc(x.name)}</td><td>${x.kind === 'reference' ? '◎ ref' : '● community'}</td>
    <td>${x.mi.toFixed(1)} mi</td><td>${x.dir}</td></tr>`).join('')
    : '<tr><td class="meta">No sensors located yet.</td></tr>';
  // permit & compliance: NPDES water dischargers link to their EPA ECHO record (air+water+waste)
  if (s.echo) {
    $('sd-permit').innerHTML = `NPDES permit <b>${esc(s.permit || '')}</b> — `
      + `<a href="${esc(s.echo)}" target="_blank" rel="noopener">full EPA ECHO compliance record `
      + `(air · water · waste) →</a>`;
  } else {
    $('sd-permit').innerHTML = 'Detailed permit &amp; compliance history coming soon (EPA ECHO / WV DEP). '
      + 'Look this facility up on '
      + '<a href="https://echo.epa.gov/facilities/facility-search" target="_blank" rel="noopener">EPA ECHO</a>.';
  }
  const q = encodeURIComponent(s.name);
  $('sd-analysis').href = `/air?src=${q}`;
  $('sd-report').href = `/air?src=${q}#report`;
  $('sd-note').textContent = DISCLAIMER;
  $('srcdetail').classList.add('on');
}

async function init() {
  // category filter options
  $('src-cat').innerHTML = '<option value="">All categories</option>'
    + CAT_ORDER.map(k => `<option value="${k}">${CAT[k].icon} ${CAT[k].label}</option>`).join('');
  try {
    const [sd, sensors, fac] = await Promise.all([
      fetch('/api/sources').then(r => r.json()),
      fetch('/api/sensors').then(r => r.json()),
      fetch('/api/facilities').then(r => r.json()),
    ]);
    SOURCES = (sd.sources || []).filter(s => s.lat != null);
    DISCLAIMER = sd.disclaimer || '';
    SENSORS = sensors || [];
    FACILITIES = fac.facilities || [];
    FAC_META = fac;
  } catch (e) { $('src-grid').innerHTML = '<p class="meta" style="padding:14px">Could not load facilities.</p>'; return; }
  $('src-disc').textContent = DISCLAIMER;
  renderCarousel();
  renderGrid();
  renderFacSummary();
  renderFacilities();
  $('fac-status').addEventListener('change', renderFacilities);
  $('fac-program').addEventListener('change', renderFacilities);
  if (!SV_KEY) {
    $('src-count').insertAdjacentHTML('afterend',
      '<span class="meta" style="margin-left:10px">📷 Street View photos appear once a Google Maps key is configured.</span>');
  }

  // interactions (event delegation for cards in both carousel and grid)
  document.body.addEventListener('click', e => {
    const b = e.target.closest('.srccard');
    if (b) openDetail(SOURCES[+b.dataset.i]);
  });
  $('src-q').addEventListener('input', renderGrid);
  $('src-cat').addEventListener('change', renderGrid);
  // deep link from the Events page: /sources#facility=<name> opens that facility
  const want = decodeURIComponent((location.hash.match(/facility=([^&]+)/) || [])[1] || '');
  if (want) { const s = SOURCES.find(x => x.name === want); if (s) openDetail(s); }
  $('sd-close').addEventListener('click', () => $('srcdetail').classList.remove('on'));
  $('srcdetail').addEventListener('click', e => { if (e.target === $('srcdetail')) $('srcdetail').classList.remove('on'); });
  const scroll = dx => $('carousel').scrollBy({left: dx, behavior: 'smooth'});
  $('car-prev').addEventListener('click', () => scroll(-660));
  $('car-next').addEventListener('click', () => scroll(660));
}
init();
