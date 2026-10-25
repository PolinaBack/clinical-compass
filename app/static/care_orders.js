(() => {
  const form = document.querySelector('[data-care-form]');
  if (!form) return;
  const kind = form.elements.kind, name = form.elements.name, due = form.elements.due;
  const show = (selector, visible, required) => {
    const label = form.querySelector(selector), input = label.querySelector('input');
    label.hidden = !visible; input.disabled = !visible; input.required = Boolean(visible && required);
  };
  const sync = () => {
    for (const group of form.querySelectorAll('[data-care-options]')) {
      const available = group.dataset.careOptions === kind.value;
      group.hidden = group.disabled = !available;
      for (const option of group.children) option.disabled = !available;
    }
    const selected = name.selectedOptions[0];
    if (!selected || selected.disabled) name.value = [...name.options].find(o => !o.disabled).value;
    show('[data-care-custom]', name.value === 'other', true);
    show('[data-care-date]', due.value === 'date', true);
    show('[data-care-period]', due.value === 'other', true);
  };
  for (const input of [kind, name, due]) input.addEventListener('change', sync);
  sync();
})();
