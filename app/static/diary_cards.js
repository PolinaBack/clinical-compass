/* Trial controls and real patient inputs use the same markup. Sliders are unnamed. */
(() => {
  function refreshEffects(box) {
    const choices = [...box.querySelectorAll('[data-effect-choice]')];
    const selected = choices.filter(item => item.checked).map(item => item.value);
    const primary = box.querySelector('[data-effect-primary]');
    const previous = primary.value;
    primary.replaceChildren(new Option('Выберите из отмеченных', ''), ...selected.map(value => new Option(value, value)));
    primary.value = selected.includes(previous) ? previous : '';
    const status = box.closest('[data-personal-card]')?.querySelector('[data-question-status]')?.value;
    primary.required = selected.length > 0 && ['unanswered', 'answered'].includes(status);
    choices.forEach(item => item.disabled = !item.checked && selected.length >= 3);
    box.querySelector('[data-effects-count]').textContent = `Выбрано ${selected.length} из 3`;
  }
  function syncCard(card) {
    card.querySelectorAll('[data-personal-sync]').forEach(slider => {
      const target = document.getElementById(slider.dataset.personalSync);
      const output = card.querySelector('[data-personal-output]');
      if (target.value !== '') slider.value = target.value;
      if (output) output.textContent = target.value || '—';
    });
    card.querySelectorAll('[data-diary-effects]').forEach(refreshEffects);
  }
  function changed(event) {
    const card = event.target.closest('[data-personal-card]');
    if (!card) return;
    if (event.target.matches('[data-personal-sync]')) document.getElementById(event.target.dataset.personalSync).value = event.target.value;
    if (event.target.matches('[data-effect-choice]') && [...card.querySelectorAll('[data-effect-choice]:checked')].length > 3) event.target.checked = false;
    syncCard(card);
  }
  document.addEventListener('input', changed);
  document.addEventListener('change', changed);
  document.querySelectorAll('[data-personal-card]').forEach(syncCard);
  document.querySelectorAll('.diary-dialog').forEach(dialog => dialog.addEventListener('close', () => dialog.querySelectorAll('[data-personal-card]').forEach(syncCard)));
})();
