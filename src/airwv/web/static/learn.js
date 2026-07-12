// Learn page tabs — show one panel at a time; deep-linkable via #basics/#health/#data/#law.
(function () {
  const tabs = [...document.querySelectorAll('.ltab')];
  const panels = [...document.querySelectorAll('.lpanel')];
  function show(name) {
    if (!document.getElementById('lp-' + name)) return;
    tabs.forEach(t => t.classList.toggle('on', t.dataset.tab === name));
    panels.forEach(p => { p.hidden = p.id !== 'lp-' + name; });
  }
  tabs.forEach(t => t.addEventListener('click', () => {
    show(t.dataset.tab);
    history.replaceState(null, '', '#' + t.dataset.tab);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }));
  const h = location.hash.slice(1);
  if (h && document.getElementById('lp-' + h)) show(h);
})();
