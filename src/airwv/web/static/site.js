// /site/<kind>/<ref> for sensors, monitors, gauges and sample sites: the recent-readings
// chart. The page puts the /api/query series-mode URL in #site-chart's data-api (the same
// URL as its CSV and API links); this fetches the rows and plots them. On every site page
// (data centers too) #site-related gets "Related nearby": the nearest site of each other
// kind from /api/query, the 5 closest. trace(), latest(), pickRelated() and relatedHtml()
// are pure so tests/test_site_js.py can run them under node.
(function (root) {
  function trace(rows, label) {
    return {x: rows.map(r => r.ts), y: rows.map(r => r.value), name: label,
            mode: rows.length > 60 ? 'lines' : 'lines+markers', line: {color: '#2c6a9e'}};
  }
  function latest(rows, label, unit) {
    if (!rows.length) return '';
    const r = rows[rows.length - 1];
    return `Latest: ${label} ${r.value}${unit ? ' ' + unit : ''} at ${String(r.ts).slice(0, 16).replace('T', ' ')} UTC`;
  }

  // every kind /api/query knows; the ones with a /site/<kind>/<ref> page link there, the
  // rest to their own record (the url /api/query gives them), as on /explore
  const KINDS = ['sensor', 'reference_monitor', 'gauge', 'sample_site', 'data_center', 'facility',
                 'permit', 'spill_report', 'water_system', 'well'];
  const PAGE_KINDS = ['sensor', 'reference_monitor', 'gauge', 'sample_site', 'data_center'];
  const KIND_LABEL = {sensor: 'Sensor', reference_monitor: 'Monitor', gauge: 'Gauge',
                      sample_site: 'Sample site', data_center: 'Data center', facility: 'Facility',
                      permit: 'Permit', spill_report: 'Spill report', water_system: 'Water system',
                      well: 'Well'};
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const mi = m => `${m < 10 ? m.toFixed(1) : m.toFixed(0)} mi`;
  const href = s => PAGE_KINDS.includes(s.kind) ? `/site/${s.kind}/${encodeURIComponent(s.ref)}` : (s.url || null);

  // lists: /api/query `sites` arrays (one per kind); the nearest of each kind that isn't
  // this page's kind (nor this site), closest first, at most n
  function pickRelated(lists, selfKind, selfId, n = 5) {
    const best = {};
    lists.flat().forEach(s => {
      if (s.kind === selfKind || s.site_id === selfId || s.mi == null) return;
      if (!best[s.kind] || s.mi < best[s.kind].mi) best[s.kind] = s;
    });
    return Object.values(best).sort((a, b) => a.mi - b.mi).slice(0, n);
  }

  function relatedHtml(sites) {
    if (!sites.length) return '<p class="meta" style="padding:14px">No other kinds of site within 25 mi.</p>';
    return '<ul class="dc-files">' + sites.map(s => {
      const h = href(s), name = esc(s.name || s.ref);
      const ext = h && !h.startsWith('/') ? ' target="_blank" rel="noopener"' : '';
      return `<li><span class="rbadge">${esc(KIND_LABEL[s.kind] || s.kind)}</span> `
        + (h ? `<a href="${esc(h)}"${ext}>${name}</a>` : name)
        + ` <span class="meta">${mi(s.mi)}${s.county ? ' · ' + esc(s.county) + ' County' : ''}</span></li>`;
    }).join('') + '</ul>';
  }

  async function related(box) {
    const {kind, site, lat, lon} = box.dataset;
    const one = k => fetch(`/api/query?lat=${lat}&lon=${lon}&km=40.25&kind=${k}&limit=1`)
      .then(r => r.ok ? r.json() : {sites: []}).then(d => d.sites || []).catch(() => []);
    const lists = await Promise.all(KINDS.filter(k => k !== kind).map(one));
    box.innerHTML = relatedHtml(pickRelated(lists, kind, site));
  }

  async function draw(box) {
    const label = box.dataset.label, unit = box.dataset.unit;
    const msg = t => { box.innerHTML = `<p class="meta" style="padding:14px">${t}</p>`; };
    let d;
    try { d = await (await fetch(box.dataset.api)).json(); }
    catch (e) { msg('Could not load the readings.'); return; }
    const rows = d.rows || [];
    if (!rows.length) { msg('No readings in this window.'); return; }
    document.getElementById('site-latest').textContent = latest(rows, label, unit);
    if (typeof Plotly === 'undefined') { msg(`${rows.length} readings; the chart library didn't load. Use the CSV link below.`); return; }
    box.innerHTML = '';
    Plotly.newPlot(box, [trace(rows, label)],
      {margin: {t: 10, r: 10, b: 40, l: 48}, height: 300,
       yaxis: {title: label + (unit ? ` (${unit})` : '')}},
      {displayModeBar: false, responsive: true});
  }

  const api = {trace, latest, pickRelated, relatedHtml};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else {
    root.AIRWV_SITE = api;
    const box = document.getElementById('site-chart');
    if (box) draw(box);
    const rel = document.getElementById('site-related');
    if (rel) related(rel);
  }
})(this);
