(() => {
  const form = document.querySelector('[data-med-preferences]');
  if (!form) return;
  const checks = [...form.querySelectorAll('[name=effect]')];
  const choices = [...form.querySelectorAll('[data-pref-choice]')];
  const notice = form.querySelector('[data-pref-limit]');
  let dirty = false;
  form.addEventListener('input', event => { if (event.target.name) dirty = true; });
  form.addEventListener('change', event => { if (event.target.name) dirty = true; });
  const sync = () => {
    const selected = checks.filter(c => c.checked).map(c => c.value);
    for (const choice of choices) {
      const available = selected.includes(choice.dataset.prefChoice);
      choice.hidden = !available;
      const radio = choice.querySelector('input');
      radio.disabled = !available;
      radio.required = available && selected.length > 0;
      if (!available) radio.checked = false;
    }
    form.querySelector('[data-pref-count]').textContent = `${selected.length} из 3`;
    form.querySelector('[data-pref-progress]').value = selected.length;
    form.querySelector('[data-pref-empty]').hidden = selected.length > 0;
  };
  checks.forEach(check => check.addEventListener('change', () => {
    if (checks.filter(c => c.checked).length > 3) {
      check.checked = false;
      notice.textContent = 'Можно выбрать максимум три эффекта. Сначала уберите один из выбранных.';
    } else notice.textContent = '';
    sync();
  }));
  form.querySelectorAll('[data-pref-remove]').forEach(button => button.addEventListener('click', () => {
    const check = checks.find(c => c.value === button.dataset.prefRemove);
    dirty = true;
    check.checked = false; sync(); notice.textContent = 'Эффект убран из выбранных.';
    const visible = checks.find(c => !c.closest('[data-pref-option]').hidden);
    (visible || form.querySelector('[data-pref-search]')).focus();
  }));
  const system = form.querySelector('[data-pref-system]'), search = form.querySelector('[data-pref-search]');
  const norm = value => String(value).normalize('NFKC').toLocaleLowerCase('ru').replaceAll('ё', 'е');
  const filter = () => {
    const words = norm(search.value).trim().split(/\s+/).filter(Boolean);
    let count = 0;
    form.querySelectorAll('[data-pref-option]').forEach(option => {
      option.hidden = Boolean((system.value && option.dataset.system !== system.value) || !words.every(w => norm(option.dataset.search).includes(w)));
      if (!option.hidden) count++;
    });
    form.querySelector('[data-pref-no-match]').hidden = count > 0;
  };
  system.addEventListener('change', filter); search.addEventListener('input', filter);
  form.addEventListener('submit', event => {
    if (event.submitter?.value === 'send' && checks.some(c => c.checked) && !form.querySelector('[name=primary]:checked')) {
      event.preventDefault(); notice.textContent = 'Отметьте один основной эффект среди выбранных.';
      choices.find(c => !c.hidden)?.querySelector('input').focus();
      return;
    }
    dirty = false;
  });
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  sync();
})();
