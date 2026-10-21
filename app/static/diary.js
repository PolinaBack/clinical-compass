/* Native dialogs, numeric alternatives and the existing durable diary form. */
(() => {
  const form = document.getElementById('diary-form');
  if (!form || !window.HTMLDialogElement || !HTMLDialogElement.prototype.showModal) return;
  document.documentElement.classList.add('diary-enhanced');
  form.noValidate = true;
  const dialogs = [...form.querySelectorAll('.diary-dialog')];
  const snapshots = new WeakMap();
  const openers = new WeakMap();
  const number = document.getElementById('wellbeing-value');
  const compass = form.querySelector('.wellbeing-compass');
  const svgNS = 'http://www.w3.org/2000/svg';
  for (let value = 0; value <= 10; value++) {
    const angle = (-135 + value * 27) * Math.PI / 180;
    const label = document.createElementNS(svgNS, 'text');
    label.setAttribute('x', 120 + Math.sin(angle) * 105);
    label.setAttribute('y', 120 - Math.cos(angle) * 105);
    label.setAttribute('text-anchor', 'middle');
    label.setAttribute('dominant-baseline', 'middle');
    label.textContent = value;
    compass.querySelector('.compass-ticks').appendChild(label);
  }
  function sync(dialog) {
    dialog.querySelectorAll('[data-sync]').forEach(slider => {
      const target = document.getElementById(slider.dataset.sync);
      if (target.value !== '') slider.value = target.value;
    });
    const value = number.value === '' ? 5 : Number(number.value);
    compass.querySelector('.compass-needle').style.transform = `rotate(${-135 + value * 27}deg)`;
    const quality = document.getElementById('sleep-quality').value;
    document.getElementById('sleep-dialog').style.setProperty('--breath-duration', `${3 + (Number(quality) || 0) / 5}s`);
  }
  function open(dialog, button) {
    openers.set(dialog, button);
    snapshots.set(dialog, [...dialog.querySelectorAll('[data-value]')].map(input => [input, input.value]));
    dialog.dataset.applied = '';
    const score = dialog.querySelector('#wellbeing-value, #sleep-quality');
    if (score && score.value === '') score.value = '5';
    sync(dialog);
    dialog.showModal();
  }
  form.querySelectorAll('[data-open]').forEach(button => button.addEventListener('click', () => open(document.getElementById(button.dataset.open), button)));
  dialogs.forEach(dialog => {
    dialog.addEventListener('keydown', event => {
      if (event.key !== 'Tab') return;
      const targets = [...dialog.querySelectorAll('a[href], button, input, select, textarea, [tabindex]')]
        .filter(element => !element.disabled && element.tabIndex >= 0 && element.getClientRects().length);
      const first = targets[0], last = targets[targets.length - 1];
      if (!first) { event.preventDefault(); return; }
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    dialog.querySelectorAll('[data-sync]').forEach(slider => slider.addEventListener('input', () => {
      document.getElementById(slider.dataset.sync).value = slider.value;
      sync(dialog);
    }));
    dialog.addEventListener('input', () => sync(dialog));
    dialog.querySelector('[data-cancel]').addEventListener('click', () => dialog.close());
    dialog.querySelector('[data-apply]').addEventListener('click', () => {
      const fields = [...dialog.querySelectorAll('[data-value]')];
      const invalid = fields.find(input => !input.checkValidity());
      if (invalid) { invalid.reportValidity(); return; }
      let preview;
      if (dialog.id === 'wellbeing-dialog') preview = `${number.value}/10`;
      else if (dialog.id === 'sleep-dialog') preview = `${form.elements.sleep_quality.value || '—'}/10 · ${form.elements.sleep.value} ч`;
      else if (fields[0].tagName === 'SELECT') preview = fields[0].selectedOptions[0].textContent;
      else preview = fields[0].value || 'Не отмечено';
      document.getElementById(dialog.dataset.preview).textContent = preview;
      dialog.dataset.applied = 'true';
      dialog.close();
      form.querySelector('.diary-status').textContent = 'Отметка добавлена. Нажмите «Сохранить запись», когда закончите.';
    });
    dialog.addEventListener('close', () => {
      if (dialog.dataset.applied !== 'true') (snapshots.get(dialog) || []).forEach(([input, value]) => { input.value = value; });
      sync(dialog);
      if (!dialogs.some(other => other.open)) openers.get(dialog)?.focus();
    });
  });
  function point(event) {
    const rect = compass.getBoundingClientRect();
    const angle = Math.atan2(event.clientX - rect.left - rect.width / 2, rect.top + rect.height / 2 - event.clientY) * 180 / Math.PI;
    number.value = Math.round((Math.max(-135, Math.min(135, angle)) + 135) / 27);
    sync(document.getElementById('wellbeing-dialog'));
  }
  compass.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    compass.setPointerCapture(event.pointerId);
    point(event);
  });
  compass.addEventListener('pointermove', event => { if (compass.hasPointerCapture(event.pointerId)) point(event); });
  form.addEventListener('submit', event => {
    const active = dialogs.find(dialog => dialog.open);
    if (active) { event.preventDefault(); active.querySelector('[data-apply]').click(); return; }
    if (form.checkValidity()) return;
    event.preventDefault();
    const invalid = form.querySelector('input:invalid, select:invalid, textarea:invalid');
    const dialog = invalid?.closest('dialog');
    if (dialog && !dialog.open) open(dialog, form.querySelector(`[data-open="${dialog.id}"]`));
    invalid?.reportValidity();
  });
})();
