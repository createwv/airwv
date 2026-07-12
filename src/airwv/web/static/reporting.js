// Shared report + feedback modal logic for every page that includes _modals.html.
// Page-agnostic: it uses window.AIRWV_MAP (set by whichever page drew a Leaflet map)
// to drop the report location pin, and calls window.AIRWV_ON_REPORT() after a
// successful report so the page can refresh whatever list it shows. Triggered by any
// element with data-open="report" | "feedback".
(function () {
  const $ = id => document.getElementById(id);
  let reportStart = 0, reportLoc = null, tmpLocMarker = null;
  const theMap = () => window.AIRWV_MAP || null;

  const openReport = () => { reportStart = reportStart || Date.now(); $('reportmodal').classList.add('on'); };
  const openFeedback = () => $('feedbackmodal').classList.add('on');
  const close = id => $(id).classList.remove('on');

  function resetReportForm() {
    ['r-category', 'r-desc', 'r-org', 'r-email'].forEach(id => { if ($(id)) $(id).value = ''; });
    reportLoc = null; reportStart = 0;
    $('r-locinfo').textContent = 'location not set';
    $('r-submit').disabled = true; $('r-result').textContent = '';
    if (tmpLocMarker) { tmpLocMarker.remove(); tmpLocMarker = null; }
  }

  function pickLocation() {
    const m = theMap();
    if (!m) {  // no map on this page — send them to the full map
      $('r-locinfo').textContent = 'Open the Analysis page to pin a spot on the map.';
      return;
    }
    $('r-locinfo').textContent = 'now click the map to drop a pin…';
    close('reportmodal');
    m.once('click', e => {
      reportLoc = e.latlng;
      if (tmpLocMarker) tmpLocMarker.remove();
      tmpLocMarker = L.marker([reportLoc.lat, reportLoc.lng]).addTo(m);
      $('r-locinfo').textContent = `📍 ${reportLoc.lat.toFixed(4)}, ${reportLoc.lng.toFixed(4)} — "Set location" to move it`;
      $('r-submit').disabled = false;
      $('reportmodal').classList.add('on');
    });
  }

  async function submitReport() {
    if (!reportLoc) return;
    $('r-submit').disabled = true;
    const body = {
      domain: $('r-domain').value, category: $('r-category').value.trim(),
      description: $('r-desc').value.trim(), lat: reportLoc.lat, lon: reportLoc.lng,
      suspected_org: $('r-org').value.trim() || null, contact_email: $('r-email').value.trim() || null,
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
    $('r-setloc').addEventListener('click', pickLocation);
    $('r-submit').addEventListener('click', submitReport);
    $('f-submit').addEventListener('click', submitFeedback);
    document.querySelectorAll('.modal').forEach(m =>
      m.addEventListener('click', e => { if (e.target === m) m.classList.remove('on'); }));
    // deep links, e.g. /analysis#report and /analysis#feedback
    if (location.hash === '#report') openReport();
    else if (location.hash === '#feedback') openFeedback();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
