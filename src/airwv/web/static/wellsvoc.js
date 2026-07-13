// "Abandoned wells near our VOC sensors" — lines up each community sensor's VOC
// with the abandoned/orphan wells around it. Self-contained (IIFE); no app.js clash.
(function () {
  const el = id => document.getElementById(id);
  const esc = s => (s || '').replace(/[&<>]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'}[c]));

  async function render() {
    let d;
    try { d = await (await fetch('/api/wells-near-sensors')).json(); }
    catch (e) { el('wv-body').innerHTML = '<tr><td colspan="5" style="color:#999">Could not load.</td></tr>'; return; }
    const rows = (d.sensors || []).filter(r => r.wells_5km > 0);   // only sensors with wells nearby
    el('wv-note').textContent = d.median_voc != null
      ? `network median VOC ${d.median_voc} · ${Number(d.well_total).toLocaleString()} abandoned wells statewide`
      : '';
    el('wv-body').innerHTML = rows.map(r => {
      const voc = r.voc == null ? '<span class="meta">—</span>'
        : `<b style="color:${r.voc_elevated ? '#c0392b' : '#137333'}">${r.voc}</b>${r.voc_elevated ? ' <span class="meta">above median</span>' : ''}`;
      const orph = r.orphans_2km ? ` <span class="meta">(${r.orphans_2km} orphan)</span>` : '';
      return `<tr>
        <td><b>${esc(r.name)}</b></td>
        <td class="meta">${esc(r.region || '')}</td>
        <td>${voc}</td>
        <td><b>${r.wells_2km}</b>${orph}</td>
        <td class="meta">${r.wells_5km}</td>
      </tr>`;
    }).join('') || '<tr><td colspan="5" style="color:#999">No sensors sit near abandoned wells.</td></tr>';
  }
  render();

  // ---- Orphan-well plugging backlog (backlog from our data · rates documented) ----
  // Documented WV plugging figures (see sources in the card):
  const STATE_RATE = 25;      // state-funded: 18 wells in 2023, 32 in 2024
  const FED_RATE = 210;       // federal IIJA/IRA: ~220 in 2023, 200+ in 2024
  const IIJA_TARGET = 1200;   // wells IIJA funding targets 2023–2030
  async function renderBacklog() {
    let d;
    try { d = await (await fetch('/api/well-backlog')).json(); }
    catch (e) { el('bl-math').textContent = 'Could not load the backlog.'; return; }
    const total = d.orphans_total || 0, nearHomes = d.orphans_near_homes || 0;
    const box = (n, txt, col) => `<span class="fac-stat" style="border-color:${col}">
      <b style="color:${col}">${Number(n).toLocaleString()}</b> ${txt}</span>`;
    el('bl-summary').innerHTML =
      box(total, 'orphan wells awaiting plugging', '#c0392b')
      + box(nearHomes, 'of them within 200 m of a building', '#e07b00')
      + box(IIJA_TARGET, 'wells the federal program targets by 2030', '#2c7fb8');
    const stateYears = Math.round(total / STATE_RATE), fedYears = Math.round(total / FED_RATE);
    el('bl-math').innerHTML =
      `At the state-funded pace (~${STATE_RATE}/yr — 18 wells in 2023, 32 in 2024), clearing today's `
      + `<b>${total.toLocaleString()}</b> orphan wells would take about <b>${stateYears} years</b>. `
      + `Federal <b>Infrastructure Investment &amp; Jobs Act</b> money (~$54M so far) lifted plugging to `
      + `~${FED_RATE}/yr (220 in 2023) — about <b>${fedYears} years</b> at that pace — but the program `
      + `targets only <b>${IIJA_TARGET.toLocaleString()}</b> wells by 2030, roughly a quarter of the backlog.`;
    const top = (d.counties || []).slice(0, 8);
    const max = Math.max(1, ...top.map(c => c.orphans));
    el('bl-bars').innerHTML = top.map(c => `
      <div style="display:flex; align-items:center; gap:8px; margin:3px 0; font-size:13px">
        <span style="width:92px; text-align:right">${esc(c.county)}</span>
        <span style="flex:1; background:#f0eceb; border-radius:3px">
          <span style="display:inline-block; height:14px; border-radius:3px; background:#c0392b; width:${Math.round(c.orphans / max * 100)}%"></span>
        </span>
        <span style="width:96px" class="meta"><b>${c.orphans}</b>${c.orphans_near_homes ? ` · ${c.orphans_near_homes} near homes` : ''}</span>
      </div>`).join('');
    el('bl-note').textContent = d.fetched_at ? `· WV DEP, as of ${d.fetched_at}` : '';
    el('bl-src').innerHTML = 'Backlog = orphan wells in the WV DEP oil &amp; gas data. Plugging figures: '
      + '<a href="https://westvirginiawatch.com/2025/10/17/wv-enters-public-private-partnership-with-diversified-energy-for-plugging-oil-gas-wells/" target="_blank" rel="noopener">WV Watch</a>, '
      + '<a href="https://dep.wv.gov/oil-and-gas/abandoned-well-plugging/infrastructure-investment-jobs-act/Pages/default.aspx" target="_blank" rel="noopener">WV DEP IIJA program</a>, '
      + '<a href="https://www.doi.gov/pressreleases/through-president-bidens-bipartisan-infrastructure-law-24-states-set-begin-plugging" target="_blank" rel="noopener">US DOI</a>.';
  }
  renderBacklog();
})();
