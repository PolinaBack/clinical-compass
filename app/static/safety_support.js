/* Safety contact prompts use raw answers. No score, model or external delivery. */
(() => {
  function urgent(values) {
    return (values['2'] === 1 && (values['4'] === 1 || values['5'] === 1)) ||
      (values['6'] === 1 && values['7'] === 1);
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {urgent};
  if (typeof document === 'undefined') return;
  const form = document.querySelector('[data-safety-values]');
  const support = document.querySelector('[data-safety-support]');
  if (form && support) {
    const saved = JSON.parse(form.dataset.safetyValues);
    const step = form.dataset.safetyStep;
    function showSupport() {
      const checked = form.querySelector(`input[name="answer_${step}"]:checked`);
      const value = checked && ['0','1'].includes(checked.value) ? Number(checked.value) : null;
      support.hidden = !urgent({...saved, [step]: value});
      support.querySelector('[data-safety-local-note]').hidden = value === saved[step];
    }
    form.addEventListener('change', showSupport);
    showSupport();
  }
  const link = document.querySelector('[data-safety-message-link]');
  if (!link) return;
  const banner = document.querySelector('[data-safety-urgent-banner]');
  const messages = document.querySelector('[data-safety-messages]');
  const delivered = new Set();
  let checksum = '', pending = false, stopped = false;
  document.addEventListener('input', event => {
    const action = event.target.closest('[data-safety-action-form]');
    if (action) action.dataset.dirty = 'true';
  });
  function updateMessages(html) {
    if (!messages) return;
    const holder = document.createElement('template');
    holder.innerHTML = html;
    const active = document.activeElement;
    const caret = active && typeof active.selectionStart === 'number'
      ? [active.selectionStart, active.selectionEnd, active.selectionDirection] : null;
    holder.content.querySelectorAll('[data-urgent-id]').forEach(fresh => {
      const old = messages.querySelector(`[data-urgent-id="${fresh.dataset.urgentId}"]`);
      const draft = old?.querySelector('[data-safety-action-form]');
      const replacement = fresh.querySelector('[data-safety-action-form]');
      if (draft && replacement && (draft.contains(active) || draft.dataset.dirty === 'true')) replacement.replaceWith(draft);
    });
    messages.replaceChildren(holder.content);
    if (active?.isConnected && messages.contains(active)) {
      active.focus({preventScroll:true});
      if (caret && active.setSelectionRange) active.setSelectionRange(...caret);
    }
  }
  async function refresh() {
    if (pending || stopped || !link.isConnected || document.hidden) return;
    pending = true;
    try {
      const response = await fetch('/api/safety-messages', {cache:'no-store',credentials:'same-origin'});
      if ([401,403].includes(response.status)) { stopped = true; throw Error(); }
      if (!response.ok) throw Error();
      const value = await response.json();
      const badge = link.querySelector('[data-safety-message-count]');
      badge.textContent = value.count;
      badge.hidden = !value.count;
      link.setAttribute('aria-label', `Сообщения специалиста. Срочных: ${value.count}`);
      link.removeAttribute('title');
      if (checksum !== value.checksum) {
        banner.innerHTML = value.banner_html;
        updateMessages(value.messages_html);
        checksum = value.checksum;
      }
      const csrf = document.querySelector('meta[name=csrf-token]').content;
      for (const id of value.deliveries) {
        if (delivered.has(id)) continue;
        const receipt = await fetch(`/events/${id}/status`, {method:'POST',credentials:'same-origin',body:new URLSearchParams({csrf,intent:'delivered'})});
        if (receipt.ok) delivered.add(id);
      }
    } catch {
      link.title = 'Не удалось обновить сообщения. Обновите страницу после восстановления связи.';
    } finally { pending = false; }
  }
  refresh();
  setInterval(refresh, 5000);
  window.addEventListener('focus', refresh);
  document.addEventListener('visibilitychange', refresh);
})();
