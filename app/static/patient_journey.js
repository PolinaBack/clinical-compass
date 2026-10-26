/* The server counts committed diary dates; this script only presents and refreshes them. */
(() => {
  const root = document.querySelector('[data-diary-journey]');
  if (!root) return;
  const bar = root.querySelector('[role=progressbar]');
  const walker = root.querySelector('.journey-walker');
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const storageKey = 'compass.diary-journey.' + root.dataset.patient;
  const position = days => (days / 90 * 100) + '%';
  let days = Number(root.dataset.days);
  function remember(value) {try {sessionStorage.setItem(storageKey, String(value));} catch (_) {}}
  try {
    const previous = sessionStorage.getItem(storageKey);
    const from = Number(previous);
    if (previous !== null && Number.isInteger(from) && from >= 0 && from < days && !reduceMotion.matches) {
      walker.style.transition = 'none';
      root.style.setProperty('--journey-position', position(from));
      requestAnimationFrame(() => requestAnimationFrame(() => {walker.style.removeProperty('transition'); root.style.setProperty('--journey-position', position(days));}));
    }
  } catch (_) {}
  remember(days);
  function update(data) {
    if (data.goal !== 90 || !Number.isInteger(data.days) || data.days < 0 || data.days > 90 || !['spring','summer','autumn','winter'].includes(data.season)) return;
    days = data.days;
    root.dataset.days = String(days);
    root.dataset.season = data.season;
    root.style.setProperty('--journey-position', position(days));
    root.querySelector('[data-journey-title]').textContent = data.season_title;
    root.querySelector('[data-journey-counter]').textContent = `${days} из 90 дней${days === 90 ? ' · путь пройден' : ''}`;
    bar.setAttribute('aria-valuenow', String(days));
    bar.setAttribute('aria-valuetext', data.value_text);
    remember(days);
  }
  let busy = false;
  async function refresh() {
    if (busy || document.hidden) return;
    busy = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 6000);
    try {
      const response = await fetch('/api/me/diary-journey', {credentials:'same-origin', cache:'no-store', signal:controller.signal});
      if (response.ok) update(await response.json());
    } catch (_) { /* Keep the last saved value when offline. */ }
    finally {clearTimeout(timeout); busy = false;}
  }
  setInterval(refresh, 60000);
  document.addEventListener('visibilitychange', () => {if (!document.hidden) refresh();});
  window.addEventListener('pageshow', refresh);
  const header = root.closest('.site-header');
  const offset = () => document.documentElement.style.setProperty('--patient-header-offset', `${Math.ceil(header.getBoundingClientRect().height) + 16}px`);
  if (window.ResizeObserver) new ResizeObserver(offset).observe(header);
  else window.addEventListener('resize', offset);
  offset();
})();
