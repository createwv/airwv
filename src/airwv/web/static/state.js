// Shared screen state in the URL (docs/DIRECTION.md): every map, chart and list reads and
// writes the same query keys, so a copied link reproduces the screen.
//   ?lat=&lon=&km=&label=   the place (label = a human name for it, e.g. a sensor)
//   &medium=air|water       which lens
//   &param=pm2_5            the measure
//   &from=&to=              time window, YYYY-MM-DD
//   &layers=sensors,ref     map layers that are on (names are per page)
//   &view=basic|adv         depth
// Other keys (e.g. /air?src=) are left alone. Values are validated on read; bad ones are dropped.
(function (root) {
  const KEYS = ['lat', 'lon', 'km', 'label', 'medium', 'param', 'from', 'to', 'layers', 'view'];
  const DATE = /^\d{4}-\d{2}-\d{2}$/;
  const WORD = /^[a-z0-9_]{1,32}$/;
  const num = (v, lo, hi) => { const n = parseFloat(v); return v !== '' && isFinite(n) && n >= lo && n <= hi ? n : null; };

  // query string → {lat, lon, km, label, medium, param, from, to, layers: [], view}; missing = null
  function parse(search) {
    const q = new URLSearchParams(search || '');
    const g = k => (q.get(k) || '').trim();
    const s = {lat: num(g('lat'), -90, 90), lon: num(g('lon'), -180, 180), km: num(g('km'), 0.1, 500),
      label: g('label').slice(0, 120) || null,
      medium: WORD.test(g('medium')) ? g('medium') : null,
      param: WORD.test(g('param')) ? g('param') : null,
      from: null, to: null,
      layers: q.has('layers') ? g('layers').split(',').map(x => x.trim()).filter(x => WORD.test(x)) : null,
      view: ['basic', 'adv'].includes(g('view')) ? g('view') : null};
    if (s.lat == null || s.lon == null) s.lat = s.lon = null;   // a place needs both
    // `from` is a date; older /nearby links used it for the place label — keep those working
    if (DATE.test(g('from'))) s.from = g('from'); else if (g('from') && !s.label) s.label = g('from').slice(0, 120);
    if (DATE.test(g('to'))) s.to = g('to');
    return s;
  }

  // merge `patch` into `search`; null/''/[] removes a key. Returns the new query string ('' or '?…').
  function merge(search, patch) {
    const q = new URLSearchParams(search || '');
    Object.keys(patch || {}).forEach(k => {
      let v = patch[k];
      if (Array.isArray(v)) v = v.join(',');
      else if (typeof v === 'number') v = k === 'lat' || k === 'lon' ? v.toFixed(4) : String(+v.toFixed(2));
      if (v == null || v === '') q.delete(k); else q.set(k, v);
    });
    // stable order: known keys first, in KEYS order, then anything else as it was
    const out = new URLSearchParams();
    KEYS.forEach(k => { if (q.has(k)) out.set(k, q.get(k)); });
    q.forEach((v, k) => { if (!KEYS.includes(k)) out.append(k, v); });
    const str = out.toString().replace(/%2C/g, ',');
    return str ? '?' + str : '';
  }

  const api = {KEYS, parse, merge,
    get: () => parse(root.location ? root.location.search : ''),
    // write without adding a history entry (filters shouldn't flood the back button)
    set(patch) {
      if (!root.location || !root.history) return;
      const qs = merge(root.location.search, patch);
      if (qs !== root.location.search) root.history.replaceState(null, '', root.location.pathname + qs + root.location.hash);
    },
    // a link to another page carrying the given state, e.g. href('/nearby', {lat, lon})
    href: (path, patch) => path + merge('', patch),
  };
  root.AirwvState = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
