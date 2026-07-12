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
  other:    {label: 'TRI facility',       icon: '🏭', color: '#4a7fb0'},
};
const CAT_ORDER = ['power', 'chemical', 'oil_gas', 'waste', 'materials', 'other'];

let SOURCES = [], SENSORS = [], DISCLAIMER = '';

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
    const [sd, sensors] = await Promise.all([
      fetch('/api/sources').then(r => r.json()),
      fetch('/api/sensors').then(r => r.json()),
    ]);
    SOURCES = (sd.sources || []).filter(s => s.lat != null);
    DISCLAIMER = sd.disclaimer || '';
    SENSORS = sensors || [];
  } catch (e) { $('src-grid').innerHTML = '<p class="meta" style="padding:14px">Could not load facilities.</p>'; return; }
  $('src-disc').textContent = DISCLAIMER;
  renderCarousel();
  renderGrid();
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
