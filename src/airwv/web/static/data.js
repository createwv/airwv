// Public data-provenance catalog (/data) — renders /api/data-catalog grouped by
// category, showing each source, access pattern, keyless status, and freshness.
(function () {
  const el = id => document.getElementById(id);
  const esc = s => (s || '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

  async function render() {
    let d;
    try { d = await (await fetch('/api/data-catalog')).json(); }
    catch (e) { el('dc-groups').innerHTML = '<p class="meta" style="padding:14px">Could not load the catalog.</p>'; return; }

    const box = (n, txt, col) => `<span class="fac-stat" style="border-color:${col}"><b style="color:${col}">${n}</b> ${txt}</span>`;
    el('dc-summary').innerHTML =
      box(d.total, 'public data sources', '#2c7fb8')
      + box(d.keyless, 'usable with no API key', '#137333')
      + box((d.categories || []).length, 'domains (air · water · facilities · …)', '#5a6472');

    el('dc-groups').innerHTML = (d.categories || []).map(cat => {
      const rows = (d.sources || []).filter(s => s.category === cat).map(s => {
        const key = s.keyless
          ? '<span class="prog-chip" style="background:#dff0e3;color:#137333">no key</span>'
          : '<span class="prog-chip" style="background:#fdeede;color:#a5590f">key needed</span>';
        const fresh = s.fetched_at ? `as of ${esc(s.fetched_at)}` : (s.freshness ? esc(s.freshness) : '');
        const count = s.count != null ? `${Number(s.count).toLocaleString()} records` : '';
        return `<tr>
          <td><a href="${esc(s.url)}" target="_blank" rel="noopener"><b>${esc(s.name)}</b></a>
            <div class="meta">${esc(s.record || '')}</div></td>
          <td class="meta">${esc(s.org || '')}</td>
          <td>${key} <span class="meta">${esc(s.access || '')}</span></td>
          <td class="meta">${[count, fresh].filter(Boolean).join(' · ')}</td>
          <td>${s.api ? `<a href="${esc(s.api)}"><code>${esc(s.api)}</code></a>` : ''}</td>
        </tr>`;
      }).join('');
      return `<div class="card"><h2>${esc(cat)}</h2>
        <table class="fac-table">
          <thead><tr><th>Source</th><th>Provider</th><th>Access</th><th>Freshness</th><th>Our API</th></tr></thead>
          <tbody>${rows}</tbody></table></div>`;
    }).join('');
  }
  render();
})();
