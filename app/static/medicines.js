(() => {
  const input = document.querySelector('#medicine-query');
  const list = document.querySelector('#medicine-suggestions');
  const status = document.querySelector('#medicine-suggestion-status');
  let timer, controller;
  input?.addEventListener('input', () => {
    clearTimeout(timer); controller?.abort(); list.replaceChildren(); status.textContent = '';
    const query = input.value.trim();
    if (query.length < 2) return;
    timer = setTimeout(async () => {
      controller = new AbortController();
      try {
        const response = await fetch('/api/medicines/suggest?q=' + encodeURIComponent(query), {signal:controller.signal});
        if (!response.ok) return;
        const {results} = await response.json();
        if (input.value.trim() !== query) return;
        list.replaceChildren(...results.map(item => {
          const option = document.createElement('option'); option.value = item.name; option.label = item.inn; return option;
        }));
        status.textContent = results.length ? `Подсказок: ${results.length}. Можно выбрать название или продолжить ввод МНН.` : 'Подсказок нет. Попробуйте другое написание.';
      } catch (error) { if (error.name !== 'AbortError') status.textContent = 'Подсказки недоступны. Поиск работает кнопкой «Найти».'; }
    }, 220);
  });
  const normalized = value => String(value || '').toLocaleLowerCase('ru').replaceAll('ё', 'е').trim();
  const toc = [...document.querySelectorAll('.medicine-toc a[href^="#medicine-"]')];
  if (toc.length) {
    const sections = toc.map(link => document.getElementById(link.hash.slice(1))).filter(Boolean);
    const active = id => toc.forEach(link => {
      if (link.hash === '#' + id) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    });
    let scheduled = false;
    const update = () => {
      scheduled = false;
      const id = [...sections].reverse().find(section => section.getBoundingClientRect().top <= 155)?.id || sections[0].id;
      active(id);
    };
    window.addEventListener('scroll', () => { if (!scheduled) { scheduled = true; requestAnimationFrame(update); } }, {passive: true});
    toc.forEach(link => link.addEventListener('click', event => {
      const target = document.getElementById(link.hash.slice(1));
      if (!target) return;
      event.preventDefault(); history.pushState(null, '', link.hash); active(target.id);
      window.scrollTo({top: scrollY + target.getBoundingClientRect().top - 125,
        behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
    }));
    update();
  }
  // The entire currently displayed catalogue is searched in place. Server GET
  // remains the fallback and can fetch a different filter's result set.
  const catalogue = document.querySelector('[data-catalog-search]');
  if (catalogue) {
    const cards = [...document.querySelectorAll('[data-catalog-card]')];
    const filter = () => {
      const words = normalized(input.value).split(/\s+/).filter(Boolean);
      const group = catalogue.elements.group.value, statusValue = catalogue.elements.status.value;
      let count = 0;
      for (const card of cards) {
        card.hidden = !words.every(w => normalized(card.dataset.search).includes(w)) || (group && card.dataset.group !== group) || (statusValue === 'verified' && card.dataset.verified !== '1') || (statusValue === 'ready' && card.dataset.prescribable !== '1') || (statusValue === 'unverified' && card.dataset.prescribable !== '0');
        if (!card.hidden) count++;
      }
      document.querySelector('[data-catalog-count]').textContent = `Найдено: ${count}. Сверка относится к отдельным сведениям и конкретной форме.`;
    };
    input.addEventListener('input', filter);
    catalogue.addEventListener('change', filter);
  }
  document.querySelector('[data-open-medicine-picker]')?.addEventListener('click', () => {
    const picker = document.querySelector('#medicine-picker');
    picker.open = true;
    picker.querySelector('input[type=search]').focus();
  });
  document.querySelectorAll('[data-medicine-picker-search]').forEach(search => {
    const picker = search.closest('.medicine-picker');
    const filter = () => {
      const words = normalized(search.value).split(/\s+/).filter(Boolean);
      let count = 0;
      picker.querySelectorAll('[data-picker-row]').forEach(row => {
        row.hidden = !words.every(w => normalized(row.dataset.search).includes(w));
        if (!row.hidden) count++;
      });
      picker.querySelector('[data-picker-status]').textContent = `Найдено: ${count}`;
    };
    search.addEventListener('input', filter);
  });
  const draft = document.querySelector('[data-medicine-draft]');
  if (draft && draft.dataset.readonly !== '1') {
    let dirty = false;
    const items = [...draft.querySelectorAll('[data-draft-item]')];
    const update = () => {
      draft.querySelector('[data-draft-empty]').hidden = items.some(i => !i.hidden);
      const blocked = items.some(item => !item.hidden && item.dataset.prescribable !== '1');
      draft.querySelectorAll('button[name=intent]').forEach(button => button.disabled = blocked);
      for (const item of items) {
        item.querySelectorAll('[data-other-select]').forEach(select => {
          const label = item.querySelector(`[data-other-field="${select.dataset.otherSelect}"]`);
          const other = ['Другая схема', 'Другое', 'Другой способ'].includes(select.value);
          label.hidden = !other;
          label.querySelector('input').disabled = !other;
          label.querySelector('input').required = other;
        });
      }
    };
    document.querySelectorAll('[data-pick-draft]').forEach(button => button.addEventListener('click', () => {
      const item = items.find(i => i.dataset.draftItem === button.dataset.pickDraft);
      if (button.disabled || item.dataset.prescribable !== '1') return;
      item.hidden = false; item.querySelector('fieldset').disabled = false;
      item.querySelector('[name=drug]').checked = true;
      button.disabled = true; button.textContent = 'Добавлено'; dirty = true; update();
      item.querySelector('input[required]').focus();
    }));
    draft.querySelectorAll('[data-remove-draft]').forEach(button => button.addEventListener('click', () => {
      const item = button.closest('[data-draft-item]');
      item.hidden = true; item.querySelector('fieldset').disabled = true; item.querySelector('[name=drug]').checked = false;
      const add = document.querySelector(`[data-pick-draft="${button.dataset.removeDraft}"]`);
      if (add) {add.disabled = item.dataset.prescribable !== '1'; add.textContent = add.disabled ? 'Не проверен' : '+ Добавить';}
      dirty = true; update();
      document.querySelector('#draft-picker').open = true; if(add&&!add.disabled)add.focus();
    }));
    draft.addEventListener('input', event => { if (event.target.form === draft) dirty = true; });
    draft.addEventListener('change', event => { if (event.target.form === draft) { dirty = true; update(); } });
    draft.addEventListener('submit', event => {
      if (!items.some(i => !i.hidden)) {
        event.preventDefault(); draft.querySelector('[data-draft-empty]').focus();
        document.querySelector('#draft-picker').open = true;
        return;
      }
      dirty = false;
    });
    window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
    update();
  }
  document.querySelectorAll('[data-medicine-verification]').forEach(form => form.addEventListener('invalid', event => {
    for(let node=event.target.parentElement;node&&node!==form.parentElement;node=node.parentElement)if(node.tagName==='DETAILS')node.open=true;
  }, true));
})();
