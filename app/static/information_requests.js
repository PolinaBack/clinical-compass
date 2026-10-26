/* Request status only: no private form values are transmitted by this poll. */
(() => {
  const controls = [...document.querySelectorAll('[data-information-request-control]')];
  const notices = [...document.querySelectorAll('[data-information-request-notice]')];
  const pid = controls[0]?.dataset.patientId || notices[0]?.dataset.patientId;
  if (!pid || !/^\d+$/.test(pid)) return;
  function apply(data) {
    if (!data.states || !Array.isArray(data.pending)) return;
    controls.forEach(box => {
      const state = data.states[box.dataset.informationRequestControl];
      if (!['received','pending','empty'].includes(state) || state === box.dataset.informationState) return;
      box.dataset.informationState = state;
      if (state === 'received') {
        const note = document.createElement('p'); note.className = 'information-request-received'; note.textContent = 'Ответ получен';
        const link = document.createElement('a'); link.href = `/patients/${pid}/profile/about/${box.dataset.category}`; link.textContent = 'Открыть сведения';
        box.replaceChildren(note,link);
      } else {
        const form = document.createElement('form'); form.method = 'post'; form.action = `/patients/${pid}/information-requests`; form.className = 'information-request-form';
        for (const [name,value] of [['csrf',document.querySelector('meta[name=csrf-token]')?.content || ''],['topic',box.dataset.informationRequestControl]]) {
          const input = document.createElement('input'); input.type = 'hidden'; input.name = name; input.value = value; form.append(input);
        }
        const button = document.createElement('button'); button.type = 'submit'; button.disabled = state === 'pending'; button.textContent = state === 'pending' ? 'Запрос отправлен' : 'Запросить у пациента'; form.append(button); box.replaceChildren(form);
      }
    });
    const pending = new Set(data.pending.map(item => String(item.id)));
    notices.forEach(notice => {
      notice.querySelectorAll('[data-information-request]').forEach(item => {if (!pending.has(item.dataset.informationRequest)) item.remove();});
      if (!notice.querySelector('[data-information-request]')) notice.remove();
    });
    document.querySelectorAll('[data-information-request-category]').forEach(badge => {if (!data.pending.some(item => item.category === badge.dataset.informationRequestCategory)) badge.remove();});
    if (!data.pending.length) document.querySelectorAll('[data-information-sent]').forEach(item => item.remove());
  }
  let busy = false;
  async function refresh() {
    if (busy || document.hidden) return;
    busy = true;
    const controller = new AbortController(); const timer = setTimeout(() => controller.abort(),6000);
    try {const response = await fetch(`/api/patients/${pid}/information-requests`,{credentials:'same-origin',cache:'no-store',signal:controller.signal}); if (response.ok) apply(await response.json());}
    catch (_) {} finally {clearTimeout(timer); busy = false;}
  }
  setInterval(refresh,10000);
  window.addEventListener('pageshow',refresh);
  document.addEventListener('visibilitychange',() => {if (!document.hidden) refresh();});
})();
