// Data-center renderers shared by /nearby and /place (rows from /api/data-centers).
// card(): the basic view, the same shape as templates/_dc_card.html (what we know / what's
// not disclosed, with sources). record(): the full record and filings, as on
// /site/data_center/<id>?view=adv (needs /api/data-centers?full=true). Pure string builders
// so tests/test_dc_js.py can run them under node.
(function (root) {
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const ext = u => `href="${esc(u)}" target="_blank" rel="noopener"`;
  const words = s => esc(String(s).replace(/_/g, ' '));

  function list(items) {
    return items.map(k => `<li>${k.value ? `<b>${esc(k.label)}:</b> ${esc(k.value)}` : esc(k.label)}</li>`).join('');
  }
  function where(c) {
    return c.mi != null ? `${c.mi} mi away` : `in ${esc(c.county)} County · exact location not public`;
  }
  function sources(c) {
    return c.sources.map(s => `<li><span class="rbadge${s.kind === 'primary' ? ' ok' : ''}">${esc(s.kind)}</span>
    <a ${ext(s.url)}>${esc(s.title)}</a> <span class="meta">${esc(s.date)}</span></li>`).join('');
  }
  function head(c) {
    return `<div class="dc-head">
      <span class="nb-title"><a href="${esc(c.url)}">${esc(c.name)}</a></span>
      <span class="rbadge">${esc(c.status_label)}</span><span class="nb-meta">${where(c)} · ${esc(c.operator)}</span></div>`;
  }

  // open air-permit comment periods (data_centers.comment_periods), same as _dc_card.html
  function comments(c) {
    return (c.comment_periods || []).map(p => `<div class="dc-due"><b>Open for public comment:</b> air permit
      <b>${esc(p.permit)}</b>, written comments due <b>${esc(p.due)}</b>.
      ${p.source ? `<a ${ext(p.source.url)}>How to comment (DEP notice) →</a>` : ''}
      <span class="meta">Deadlines can change; check the notice.</span></div>`).join('');
  }

  function card(c) {
    const topics = c.topics.map(t => `<div class="dc-topic"><h3>${esc(t.label)}</h3>
    ${t.known.length ? `<div class="dc-sub">What we know</div><ul>${list(t.known)}</ul>` : ''}
    ${t.gaps.length ? `<div class="dc-sub dc-gap">Not disclosed or not found</div><ul>${list(t.gaps)}</ul>` : ''}
    ${!t.known.length && !t.gaps.length ? '<p class="meta">Nothing in the public record we found.</p>' : ''}</div>`).join('');
    return `<div class="dc-card">${head(c)}${comments(c)}
    <div class="dc-grid">${topics}</div>
    <details class="dc-sources"><summary><b>Data sources (${c.sources.length})</b> <span class="meta">primary = agency or court record; news can be wrong or out of date</span></summary><ul>${sources(c)}</ul></details>
    <div class="nb-actions" style="margin-top:6px"><a href="${esc(c.url)}">plain summary →</a><a href="${esc(c.url)}?view=adv">full record and filings →</a></div>
  </div>`;
  }

  // the rows of templates/site.html's "Full record" table
  function record(c) {
    const d = c.record || {};
    const tr = (th, td) => `<tr><th>${th}</th><td>${td}</td></tr>`;
    const rows = [
      tr('Status', `${esc(c.status_label)} — ${esc(d.status_detail)}`),
      tr('Operator', esc(d.operator)),
      tr('Place', `${esc(d.place)} (${esc(d.county)} County)`),
      tr('Location', `${d.lat != null ? `${esc(d.lat)}, ${esc(d.lon)} — ` : ''}${esc(d.location_precision)}`),
      tr('Acres', esc(d.acres)),
      tr('HB 2014 certification', esc(d.hb2014)),
    ];
    [['power', 'Power'], ['water', 'Water'], ['air_permits', 'Air permits'], ['noise', 'Noise']].forEach(([sec, label]) =>
      Object.entries(d[sec] || {}).forEach(([k, v]) => rows.push(tr(`${label} · ${words(k)}`, esc(v)))));
    (d.use || []).forEach(u => rows.push(tr(`Use · ${words(u.category)}`, `${esc(u.confidence)} confidence — ${esc(u.evidence)}`)));
    rows.push(tr('Not disclosed', esc((d.not_disclosed || []).join('; '))));
    return `<div class="dc-card">${head(c)}${comments(c)}<p class="meta">${esc(d.status_detail)}</p></div>
    <table class="dc-rec">${rows.join('')}</table>
    <div class="dc-card"><b>Filings and data sources for this site</b><ul class="dc-files">${sources(c)}</ul></div>`;
  }

  function ask(items) {
    return `<div class="dc-ask"><b>Where the data doesn't exist, you can ask</b><ul>${items.map(a =>
      `<li><b>${a.url ? `<a ${ext(a.url)}>${esc(a.where)}</a>` : esc(a.where)}:</b> ${esc(a.how)}</li>`).join('')}</ul></div>`;
  }

  function dockets(items) {
    return `<ul class="dc-files">${items.map(d => `<li><b>${esc(d.agency)} ${esc(d.id)}</b> —
      ${d.url ? `<a ${ext(d.url)}>${esc(d.title)}</a>` : esc(d.title)}<div class="meta">${esc(d.detail)}</div></li>`).join('')}</ul>`;
  }

  const api = {card, record, comments, ask, dockets};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.AIRWV_DC = api;
})(this);
