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
})();
