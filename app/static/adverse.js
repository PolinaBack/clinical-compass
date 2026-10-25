(() => {
  const norm = value => String(value || '').normalize('NFKC').toLocaleLowerCase('ru').replaceAll('ё', 'е').trim();
  const plural = n => `${n} ${n % 100 >= 11 && n % 100 <= 14 ? 'записей' : n % 10 === 1 ? 'запись' : n % 10 >= 2 && n % 10 <= 4 ? 'записи' : 'записей'}`;
  const page = document.querySelector('[data-adverse-page]');
  if (page) {
    const form = page.querySelector('[data-adverse-filters]');
    const groups = [...page.querySelectorAll('[data-adverse-system]')];
    const rows = [...page.querySelectorAll('[data-adverse-row]')];
    function filter() {
      const drug = form.elements.drug.value, system = form.elements.system.value;
      const query = form.elements.q.value.slice(0, 200), words = norm(query).split(/\s+/).filter(Boolean);
      const params = new URLSearchParams({drug, system, q: query});
      const source = '/medicines/adverse-effects?' + params;
      let count = 0;
      for (const row of rows) {
        row.hidden = Boolean((drug && row.dataset.drug !== drug) || (system && row.dataset.system !== system) || !words.every(w => norm(row.dataset.search).includes(w)));
        if (!row.hidden) count++;
        const link = row.querySelector('[data-adverse-card]');
        const url = new URL(link.dataset.adverseCard, location.origin);
        url.searchParams.set('return_to', source); url.hash = 'medicine-registry'; link.href = url.pathname + url.search + url.hash;
      }
      for (const group of groups) {
        const size = [...group.querySelectorAll('[data-adverse-row]')].filter(row => !row.hidden).length;
        group.hidden = size === 0; group.querySelector('[data-adverse-group-count]').textContent = plural(size);
      }
      page.querySelector('[data-adverse-count]').textContent = `Найдено ${plural(count)} из ${rows.length}.`;
      page.querySelector('[data-adverse-empty]').hidden = count > 0;
      history.replaceState(null, '', source);
    }
    form.addEventListener('input', filter); form.addEventListener('change', filter);
    form.addEventListener('submit', event => { event.preventDefault(); filter(); });
  }
  const picker = document.querySelector('[data-tolerability-picker]');
  if (picker) {
    const form = picker.closest('form'), primary = form.querySelector('[name="a_primary_reaction"]');
    const checks = [...picker.querySelectorAll('input[type=checkbox]')];
    const status = picker.querySelector('[data-tolerability-status]');
    const rejected = new WeakSet();
    const sync = () => {
      const selected = checks.filter(c => c.checked).map(c => c.value);
      if (primary) {
        for (const option of primary.options) if (option.value) option.hidden = option.disabled = !selected.includes(option.value);
        if (primary.value && !selected.includes(primary.value)) {
          primary.value = ''; form.querySelector('[data-intake-field="primary_reaction"] [data-intake-state]').value = 'unanswered';
        }
      }
      status.textContent = `Выбрано ${selected.length} из 3. Главный приоритет можно отметить ниже.`;
    };
    for (const name of ['input', 'change']) picker.addEventListener(name, event => {
      if (!event.target.matches('input[type=checkbox]')) return;
      if (rejected.has(event.target)) { rejected.delete(event.target); event.stopPropagation(); return; }
      if (checks.filter(c => c.checked).length > 3) {
        event.target.checked = false; rejected.add(event.target); event.stopPropagation();
        status.textContent = 'Можно выбрать максимум три эффекта. Чтобы добавить другой, сначала уберите один из выбранных.';
      } else sync();
    }, true);
    const search = picker.querySelector('[data-tolerability-search]'), system = picker.querySelector('[data-tolerability-system]');
    form.addEventListener('change', event => {
      if (event.target.matches('[data-intake-field="avoid_reactions"] [data-intake-state]') && event.target.value !== 'answered' && primary) {
        checks.forEach(check => check.checked = false);
        primary.value = ''; form.querySelector('[data-intake-field="primary_reaction"] [data-intake-state]').value = 'unanswered';
        sync();
      }
    });
    for (const input of [search, system]) for (const name of ['input', 'change']) input.addEventListener(name, event => {
      event.stopPropagation();
      const words = norm(search.value).split(/\s+/).filter(Boolean);
      picker.querySelectorAll('[data-tolerability-option]').forEach(option => option.hidden = Boolean((system.value && option.dataset.system !== system.value) || !words.every(w => norm(option.dataset.search).includes(w))));
    });
    sync();
  }
})();
