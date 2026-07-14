// Shared report + feedback modal logic for every page that includes _modals.html.
// Page-agnostic: it uses window.AIRWV_MAP (set by whichever page drew a Leaflet map)
// to drop the report location pin, and calls window.AIRWV_ON_REPORT() after a
// successful report so the page can refresh whatever list it shows. Triggered by any
// element with data-open="report" | "feedback".
(function () {
  const $ = id => document.getElementById(id);
  let reportStart = 0, reportLoc = null, reportArea = null, tmpLocMarker = null, photoData = null;
  const theMap = () => window.AIRWV_MAP || null;

  const openReport = () => { reportStart = reportStart || Date.now(); $('reportmodal').classList.add('on'); };
  const openFeedback = () => $('feedbackmodal').classList.add('on');
  const close = id => $(id).classList.remove('on');

  // Public hook: open the report modal pre-filled and pre-located — used by the
  // "report this well" link on abandoned-well popups (no map-pick needed).
  window.AIRWV_REPORT_AT = (opts = {}) => {
    if (!$('reportmodal')) return;
    resetReportForm();
    reportStart = Date.now();
    if (opts.lat != null && opts.lon != null) setLocation(opts.lat, opts.lon);
    if (opts.domain && $('r-domain')) $('r-domain').value = opts.domain;
    if (opts.category) $('r-category').value = opts.category;
    if (opts.description) $('r-desc').value = opts.description;
    $('reportmodal').classList.add('on');
  };

  function resetReportForm() {
    ['r-category', 'r-desc', 'r-name', 'r-email', 'r-phone', 'r-addr'].forEach(id => { if ($(id)) $(id).value = ''; });
    reportLoc = null; reportArea = null; reportStart = 0; photoData = null;
    if ($('r-photo')) $('r-photo').value = '';
    if ($('r-photoinfo')) $('r-photoinfo').textContent = '';
    if ($('r-rows')) $('r-rows').innerHTML = '';
    $('r-locinfo').textContent = 'location not set';
    $('r-submit').disabled = true; $('r-result').textContent = '';
    if (tmpLocMarker) { tmpLocMarker.remove(); tmpLocMarker = null; }
  }

  // --- location: my-location · map-pick · address, all funnel through setLocation ---
  function setLocation(lat, lon, label) {
    reportLoc = { lat: +lat, lng: +lon };
    const m = theMap();
    if (m) {
      if (tmpLocMarker) tmpLocMarker.remove();
      tmpLocMarker = L.marker([lat, lon]).addTo(m);
      m.setView([lat, lon], Math.max(m.getZoom() || 0, 12));
    }
    $('r-locinfo').textContent = `📍 ${(+lat).toFixed(4)}, ${(+lon).toFixed(4)}${label ? ' · ' + label : ''}`;
    $('r-submit').disabled = false;
    if (label) reportArea = label;
    else reverseGeocode(lat, lon);   // best-effort coarse place name
  }

  function useMyLocation() {
    if (!navigator.geolocation) { $('r-locinfo').textContent = 'geolocation not available on this device'; return; }
    $('r-locinfo').textContent = 'finding your location…';
    navigator.geolocation.getCurrentPosition(
      p => setLocation(p.coords.latitude, p.coords.longitude),
      () => { $('r-locinfo').textContent = 'could not get your location — check permissions, or pick on the map'; },
      { enableHighAccuracy: true, timeout: 10000 });
  }

  function pickLocation() {
    const m = theMap();
    if (!m) { $('r-locinfo').textContent = 'No map here — use "Use my location" or type an address.'; return; }
    $('r-locinfo').textContent = 'now click the map to drop a pin…';
    close('reportmodal');
    m.once('click', e => { setLocation(e.latlng.lat, e.latlng.lng); $('reportmodal').classList.add('on'); });
  }

  async function findAddress() {
    const q = ($('r-addr').value || '').trim();
    if (!q) return;
    $('r-locinfo').textContent = 'looking up address…';
    try {                            // OpenStreetMap Nominatim, biased to WV's bounding box
      const url = 'https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&countrycodes=us'
        + '&viewbox=-82.7,40.7,-77.7,37.1&q=' + encodeURIComponent(q);
      const d = await (await fetch(url, { headers: { Accept: 'application/json' } })).json();
      if (d && d.length) setLocation(+d[0].lat, +d[0].lon, coarsePlace(d[0]));
      else $('r-locinfo').textContent = 'address not found — try a nearby town, or pick on the map';
    } catch (e) { $('r-locinfo').textContent = 'address lookup failed — pick on the map instead'; }
  }

  async function reverseGeocode(lat, lon) {
    try {
      const url = `https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=12&lat=${lat}&lon=${lon}`;
      const d = await (await fetch(url, { headers: { Accept: 'application/json' } })).json();
      const label = coarsePlace(d);
      if (label && reportLoc) {
        reportArea = label;
        $('r-locinfo').textContent = `📍 ${(+lat).toFixed(4)}, ${(+lon).toFixed(4)} · ${label}`;
      }
    } catch (e) { /* coords alone are fine */ }
  }

  // Coarse place only (town + county) — never a house number/street, for privacy.
  function coarsePlace(d) {
    const a = (d && d.address) || {};
    const town = a.city || a.town || a.village || a.hamlet || a.municipality || null;
    const parts = [town, a.county].filter(Boolean);
    return parts.length ? parts.join(', ') : (a.state || null);
  }

  // --- optional structured measurements ----------------------------------
  function addRow() {
    $('r-rows').insertAdjacentHTML('beforeend',
      `<div class="rrow">
        <input class="rr-param" maxlength="64" placeholder="what (VOC, pH, conductivity…)">
        <input class="rr-val" type="number" step="any" placeholder="value">
        <input class="rr-unit" maxlength="32" placeholder="unit">
        <button type="button" class="rr-del" title="remove">×</button></div>`);
    const row = $('r-rows').lastElementChild;
    row.querySelector('.rr-del').addEventListener('click', () => row.remove());
  }

  function collectReadings() {
    return [...$('r-rows').querySelectorAll('.rrow')].map(r => ({
      parameter: r.querySelector('.rr-param').value.trim(),
      value: parseFloat(r.querySelector('.rr-val').value),
      unit: r.querySelector('.rr-unit').value.trim(),
    })).filter(x => x.parameter && !isNaN(x.value));
  }

  // --- optional photo (base64 data URL, capped 8 MB) ---------------------
  function onPhoto() {
    const f = $('r-photo').files[0];
    if (!f) { photoData = null; $('r-photoinfo').textContent = ''; return; }
    if (f.size > 8 * 1024 * 1024) { $('r-photoinfo').textContent = 'image too large (max 8 MB)'; $('r-photo').value = ''; photoData = null; return; }
    const reader = new FileReader();
    reader.onload = () => { photoData = reader.result; $('r-photoinfo').textContent = `📷 ${f.name} attached (held for review)`; };
    reader.readAsDataURL(f);
  }

  async function submitReport() {
    if (!reportLoc) { $('r-result').textContent = 'Set a location first.'; return; }
    $('r-submit').disabled = true;
    const body = {
      domain: $('r-domain').value, category: $('r-category').value.trim(),
      description: $('r-desc').value.trim(), lat: reportLoc.lat, lon: reportLoc.lng,
      area_label: reportArea || null,
      contact_name: $('r-name').value.trim() || null,
      contact_email: $('r-email').value.trim() || null,
      contact_phone: $('r-phone').value.trim() || null,
      readings: collectReadings(), photo: photoData || null,
      website: $('r-website').value, elapsed_ms: Date.now() - reportStart,
    };
    try {
      const res = await fetch('/api/reports', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const d = await res.json();
      if (res.ok) {
        $('r-result').textContent = d.message || 'Thanks!';
        if (window.AIRWV_ON_REPORT) window.AIRWV_ON_REPORT();
        setTimeout(() => { close('reportmodal'); resetReportForm(); }, 2200);
      } else { $('r-result').textContent = typeof d.detail === 'string' ? d.detail : 'Could not submit.'; $('r-submit').disabled = false; }
    } catch (e) { $('r-result').textContent = 'Error submitting — try again.'; $('r-submit').disabled = false; }
  }

  async function submitFeedback() {
    if (!$('f-message').value.trim()) { $('f-result').textContent = 'Please add a message.'; return; }
    $('f-submit').disabled = true;
    try {
      const res = await fetch('/api/feedback', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          kind: $('f-kind').value, message: $('f-message').value.trim(),
          contact: $('f-contact').value.trim() || null, page: location.pathname, website: $('f-website').value,
        }),
      });
      const d = await res.json();
      $('f-result').textContent = res.ok ? (d.message || 'Thanks!') : (d.detail || 'Could not send.');
      if (res.ok) setTimeout(() => { close('feedbackmodal'); $('f-message').value = ''; $('f-result').textContent = ''; $('f-submit').disabled = false; }, 1800);
      else $('f-submit').disabled = false;
    } catch (e) { $('f-result').textContent = 'Error — try again.'; $('f-submit').disabled = false; }
  }

  function init() {
    if (!$('reportmodal')) return;  // page didn't include the modals
    document.querySelectorAll('[data-open="report"]').forEach(el =>
      el.addEventListener('click', e => { e.preventDefault(); openReport(); }));
    document.querySelectorAll('[data-open="feedback"]').forEach(el =>
      el.addEventListener('click', e => { e.preventDefault(); openFeedback(); }));
    $('closereport').addEventListener('click', () => close('reportmodal'));
    $('closefeedback').addEventListener('click', () => close('feedbackmodal'));
    $('r-useloc').addEventListener('click', useMyLocation);
    $('r-setloc').addEventListener('click', pickLocation);
    $('r-addrfind').addEventListener('click', findAddress);
    $('r-addr').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); findAddress(); } });
    $('r-addrow').addEventListener('click', addRow);
    $('r-photo').addEventListener('change', onPhoto);
    $('r-submit').addEventListener('click', submitReport);
    $('f-submit').addEventListener('click', submitFeedback);
    document.querySelectorAll('.modal').forEach(m =>
      m.addEventListener('click', e => { if (e.target === m) m.classList.remove('on'); }));
    // deep links, e.g. /air#report and /air#feedback
    if (location.hash === '#report') openReport();
    else if (location.hash === '#feedback') openFeedback();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
