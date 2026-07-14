// "What's near me?" — geolocate, aggregate nearby hazards from every layer, and
// highlight the categories that match the symptoms someone selects. Not medical advice.
const $ = id => document.getElementById(id);
const esc = s => (s || '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

const CAT = {
  gas:      {label: 'Gas & wells', color: '#c0392b', note: 'Abandoned/orphan gas wells can vent natural gas & H2S.'},
  air:      {label: 'Industrial air & dust', color: '#7a5b12', note: 'Facilities, mining, and TRI sites.'},
  water:    {label: 'Water', color: '#2c7fb8', note: 'Discharges, measured samples, drinking-water systems.'},
  chemical: {label: 'Reported spills', color: '#8e44ad', note: 'Oil/chemical releases reported to the NRC.'},
  sensor:   {label: 'Nearby sensors', color: '#2a7a2a', note: 'What our community air sensors read right now.'},
};
const SYMPTOMS = [
  {id: 'rotten_egg', label: '👃 Rotten-egg / sulfur smell', cats: ['gas']},
  {id: 'headache', label: '🤕 Headache, dizziness, nausea', cats: ['gas', 'air', 'chemical']},
  {id: 'breathing', label: '😮‍💨 Cough, shortness of breath, chest tightness', cats: ['air', 'gas', 'sensor']},
  {id: 'irritation', label: '😣 Eye, nose or throat irritation', cats: ['air', 'gas', 'chemical']},
  {id: 'skin', label: '🩹 Skin rash or burning', cats: ['chemical', 'gas', 'water']},
  {id: 'water_bad', label: '🚰 Bad-tasting / smelling / discolored water', cats: ['water']},
  {id: 'stomach', label: '🤢 Stomach / GI illness', cats: ['water']},
];
const REPORT_DOMAIN = {gas: 'air', air: 'air', water: 'water', chemical: 'other', sensor: 'air'};

let picked = new Set(), DATA = null, nbMap, nbLayer;

function relevantCats() {
  const c = new Set();
  SYMPTOMS.filter(s => picked.has(s.id)).forEach(s => s.cats.forEach(x => c.add(x)));
  return c;
}

function renderSymptoms() {
  $('nb-symptoms').innerHTML = SYMPTOMS.map(s =>
    `<button class="nb-chip${picked.has(s.id) ? ' on' : ''}" data-sym="${s.id}">${s.label}</button>`).join('');
  $('nb-symptoms').querySelectorAll('[data-sym]').forEach(b => b.addEventListener('click', () => {
    picked.has(b.dataset.sym) ? picked.delete(b.dataset.sym) : picked.add(b.dataset.sym);
    b.classList.toggle('on');
    if (DATA) renderResults();
  }));
}

function hazardRow(h, relevant) {
  const flag = h.flag ? `<span class="nb-flag nb-${h.flag}">${h.flag.replace('_', ' ')}</span>` : '';
  const dist = h.mi != null ? `${h.mi} mi` : 'in your county';
  const link = h.link ? `<a href="${esc(h.link)}" ${h.link.startsWith('http') ? 'target="_blank" rel="noopener"' : ''}>details →</a>` : '';
  const report = h.report ? `<a href="#" class="nb-report" data-lat="${h.lat}" data-lon="${h.lon}" data-cat="${h.category}" data-label="${esc(h.label)}">report →</a>` : '';
  return `<div class="nb-item${relevant ? ' rel' : ''}">
    <span class="nb-ic">${h.icon || '•'}</span>
    <div class="nb-body">
      <div class="nb-title">${esc(h.label)} ${flag}</div>
      <div class="nb-meta">${dist}${h.detail ? ' · ' + esc(h.detail) : ''}</div>
    </div>
    <div class="nb-actions">${link} ${report}</div>
  </div>`;
}

function renderResults() {
  const rel = relevantCats();
  const groups = {};
  (DATA.hazards || []).forEach(h => (groups[h.category] = groups[h.category] || []).push(h));
  // relevant categories first, then the rest; each sorted by distance
  const order = Object.keys(CAT).sort((a, b) => (rel.has(b) - rel.has(a)));
  const html = order.filter(c => groups[c] && groups[c].length).map(c => {
    const items = groups[c].sort((a, b) => (a.mi == null) - (b.mi == null) || (a.mi || 0) - (b.mi || 0));
    const isRel = rel.has(c);
    return `<div class="card nb-group${isRel ? ' rel' : ''}">
      <h2><span style="color:${CAT[c].color}">●</span> ${CAT[c].label} <span class="meta">${items.length}</span>
        ${isRel ? '<span class="nb-match">matches your symptoms</span>' : ''}</h2>
      <p class="meta" style="padding:0 14px 4px">${CAT[c].note}</p>
      <div class="nb-list">${items.map(h => hazardRow(h, isRel)).join('')}</div>
    </div>`;
  }).join('');
  $('nb-results').innerHTML = html || '<div class="card"><p class="meta" style="padding:16px">Good news — nothing we track is mapped within this radius. Widen it, or if you smell or see something, <a href="#" data-open="report">report it →</a>.</p></div>';
  drawMap();
  if (rel.size && html) $('nb-results').insertAdjacentHTML('afterbegin',
    `<p class="meta" style="margin:4px 2px 8px">⬆ Categories that fit your symptoms are shown first and marked. This is a place to look — not a cause.</p>`);
  wireReports();
}

function drawMap() {
  $('nb-mapcard').style.display = 'block';
  if (!nbMap) {
    nbMap = L.map('nb-map').setView([DATA.lat, DATA.lon], 12);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 18, attribution: '© OpenStreetMap'}).addTo(nbMap);
  }
  nbMap.setView([DATA.lat, DATA.lon], 12);
  if (nbLayer) nbLayer.remove();
  nbLayer = L.layerGroup();
  L.marker([DATA.lat, DATA.lon], {icon: L.divIcon({className: '', html: '📍', iconSize: [26, 26], iconAnchor: [13, 26]})})
    .bindPopup('You are here').addTo(nbLayer);
  (DATA.hazards || []).forEach(h => {
    if (h.lat == null || h.lon == null) return;
    L.circleMarker([h.lat, h.lon], {radius: 6, color: '#333', weight: 1, fillColor: CAT[h.category].color, fillOpacity: 0.85})
      .bindPopup(`<b>${esc(h.label)}</b><br><small>${h.mi != null ? h.mi + ' mi · ' : ''}${esc(h.detail || '')}</small>`)
      .addTo(nbLayer);
  });
  nbLayer.addTo(nbMap);
  setTimeout(() => nbMap.invalidateSize(), 100);
}

function wireReports() {
  $('nb-results').querySelectorAll('.nb-report').forEach(a => a.addEventListener('click', ev => {
    ev.preventDefault();
    const d = a.dataset;
    if (window.AIRWV_REPORT_AT) window.AIRWV_REPORT_AT({
      lat: +d.lat, lon: +d.lon, domain: REPORT_DOMAIN[d.cat] || 'other',
      category: d.label.slice(0, 60),
      description: `Reporting a concern near "${d.label}". What did you notice, and how do you feel? `,
    });
  }));
}

// Shared loader — fetch nearby hazards for an explicit point and render.
// `from` is an optional human label (e.g. a sensor name) shown in the location line.
async function loadAt(lat, lon, from) {
  const km = $('nb-km').value;
  $('nb-locinfo').textContent = `📍 ${(+lat).toFixed(3)}, ${(+lon).toFixed(3)}${from ? ' · ' + from : ''}`;
  try {
    DATA = await (await fetch(`/api/near?lat=${lat}&lon=${lon}&km=${km}`)).json();
    $('nb-locinfo').textContent += ` — ${(DATA.hazards || []).length} things nearby${DATA.county ? ' · ' + DATA.county + ' County' : ''}`;
    renderResults();
  } catch (e) { $('nb-results').innerHTML = '<div class="card"><p class="meta" style="padding:16px">Could not load nearby data — try again.</p></div>'; }
}

function locate() {
  if (!navigator.geolocation) { $('nb-locinfo').textContent = 'geolocation not available on this device'; return; }
  $('nb-locinfo').textContent = 'finding your location…';
  navigator.geolocation.getCurrentPosition(
    p => loadAt(p.coords.latitude, p.coords.longitude),
    () => { $('nb-locinfo').textContent = 'could not get your location — check browser permissions'; },
    {enableHighAccuracy: true, timeout: 10000});
}

renderSymptoms();
$('nb-loc').addEventListener('click', locate);
$('nb-km').addEventListener('change', () => { if (DATA) loadAt(DATA.lat, DATA.lon); });

// Deep-link support: /nearby?lat=&lon=[&km=][&from=] auto-loads that point
// (e.g. drilling in from a sensor reading on the Air page).
(function () {
  const q = new URLSearchParams(location.search);
  const lat = q.get('lat'), lon = q.get('lon');
  if (lat && lon && !isNaN(+lat) && !isNaN(+lon)) {
    if (q.get('km') && $('nb-km')) $('nb-km').value = q.get('km');
    loadAt(lat, lon, q.get('from') || '');
  }
})();
