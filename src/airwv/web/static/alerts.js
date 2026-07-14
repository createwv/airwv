// Environmental alert sign-up: choose metrics + severity + an area, then POST.
const $ = id => document.getElementById(id);
const started = Date.now();
let center = null, area = null;   // {lat, lon} + coarse place label

function setLoc(lat, lon, label) {
  center = { lat: +lat, lon: +lon };
  area = label || null;
  $('a-locinfo').textContent = `📍 ${(+lat).toFixed(3)}, ${(+lon).toFixed(3)}${label ? ' · ' + label : ''}`;
}

function useMyLocation() {
  if (!navigator.geolocation) { $('a-locinfo').textContent = 'geolocation not available on this device'; return; }
  $('a-locinfo').textContent = 'finding your location…';
  navigator.geolocation.getCurrentPosition(
    p => { setLoc(p.coords.latitude, p.coords.longitude); reverseGeocode(p.coords.latitude, p.coords.longitude); },
    () => { $('a-locinfo').textContent = 'could not get your location — type a town instead'; },
    { enableHighAccuracy: true, timeout: 10000 });
}

async function findAddress() {
  const q = ($('a-addr').value || '').trim();
  if (!q) return;
  $('a-locinfo').textContent = 'looking up location…';
  try {
    const url = 'https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&countrycodes=us'
      + '&viewbox=-82.7,40.7,-77.7,37.1&q=' + encodeURIComponent(q);
    const d = await (await fetch(url, { headers: { Accept: 'application/json' } })).json();
    if (d && d.length) setLoc(+d[0].lat, +d[0].lon, coarsePlace(d[0]));
    else $('a-locinfo').textContent = 'not found — try a nearby town';
  } catch (e) { $('a-locinfo').textContent = 'lookup failed — try again'; }
}

async function reverseGeocode(lat, lon) {
  try {
    const url = `https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=12&lat=${lat}&lon=${lon}`;
    const d = await (await fetch(url, { headers: { Accept: 'application/json' } })).json();
    const label = coarsePlace(d);
    if (label && center) { area = label; $('a-locinfo').textContent = `📍 ${(+lat).toFixed(3)}, ${(+lon).toFixed(3)} · ${label}`; }
  } catch (e) { /* coords alone are fine */ }
}

function coarsePlace(d) {
  const a = (d && d.address) || {};
  const town = a.city || a.town || a.village || a.hamlet || a.municipality || null;
  return [town, a.county].filter(Boolean).join(', ') || (a.state || null);
}

async function submit(e) {
  e.preventDefault();
  const btn = $('a-submit'), note = $('a-note');
  const email = $('a-email').value.trim();
  if (!email) { note.style.color = '#b00'; note.textContent = 'Please enter your email.'; return; }
  const metrics = [...document.querySelectorAll('.a-metric:checked')].map(c => c.value);
  if (!metrics.length) { note.style.color = '#b00'; note.textContent = 'Pick at least one thing to watch.'; return; }
  const level = (document.querySelector('input[name="level"]:checked') || {}).value || 'unhealthy';
  const radius = $('a-radius').value;                 // '' = anywhere in WV
  const useArea = radius && center;                   // need both a radius and a point
  const payload = {
    email, metrics, level,
    lat: useArea ? center.lat : null, lon: useArea ? center.lon : null,
    radius_mi: useArea ? +radius : null,
    label: useArea ? (area || `within ${radius} mi`) : 'anywhere in WV',
    website: $('a-website').value, elapsed_ms: Date.now() - started,
  };
  btn.disabled = true; note.style.color = ''; note.textContent = 'Signing you up…';
  try {
    const r = await fetch('/api/alerts/subscribe', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    const d = await r.json();
    if (!r.ok) { note.style.color = '#b00'; note.textContent = d.detail || 'Something went wrong.'; btn.disabled = false; return; }
    note.style.color = '#137333';
    note.textContent = d.message;
    $('alertform').querySelectorAll('input,select,button').forEach(el => { el.disabled = true; });
  } catch (err) {
    note.style.color = '#b00'; note.textContent = 'Network error — please try again.'; btn.disabled = false;
  }
}

$('a-useloc').addEventListener('click', useMyLocation);
$('a-addrfind').addEventListener('click', findAddress);
$('a-addr').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); findAddress(); } });
$('alertform').addEventListener('submit', submit);
