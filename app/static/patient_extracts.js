(() => {
  document.querySelectorAll('[data-print-document]').forEach(button => {
    button.addEventListener('click', () => window.print());
    if (button.hasAttribute('data-auto-print')) window.addEventListener('load', () => window.print(), {once: true});
  });
  document.querySelectorAll('[data-document-request]').forEach(requestForm => {
    const rows = requestForm.querySelector('[data-request-rows]');
    const add = requestForm.querySelector('[data-add-request-document]');
    function renumber() {
      const items = [...rows.querySelectorAll('[data-request-row]')];
      items.forEach((row, index) => {
        row.querySelector('[data-request-row-title]').textContent = 'Документ ' + (index + 1);
        row.querySelectorAll('[data-document-field]').forEach(input => input.name = 'doc_' + index + '_' + input.dataset.documentField);
        row.querySelector('[data-remove-request-document]').hidden = items.length === 1;
        row.querySelector('[data-request-date]').required = row.querySelector('[data-request-due]').value === 'date';
      });
      requestForm.querySelector('[data-document-count]').value = items.length;
      requestForm.querySelector('[data-request-count]').textContent = 'Документов в заказе: ' + items.length;
      add.disabled = items.length >= 50;
    }
    rows.addEventListener('change', renumber);
    rows.addEventListener('click', event => {
      const remove = event.target.closest('[data-remove-request-document]');
      if (remove && rows.querySelectorAll('[data-request-row]').length > 1) {remove.closest('[data-request-row]').remove(); renumber();}
    });
    add.addEventListener('click', () => {
      if (rows.querySelectorAll('[data-request-row]').length >= 50) return;
      rows.appendChild(requestForm.querySelector('[data-request-row-template]').content.cloneNode(true));
      renumber(); rows.lastElementChild.querySelector('select').focus();
    });
    add.hidden = false; renumber();
  });
  const form = document.querySelector('[data-extract-editor]');
  if (!form) return;
  const review = form.elements.reviewed;
  function badges() {
    form.querySelectorAll('[data-review-badge]').forEach(badge => {
      badge.dataset.reviewed = String(review.checked);
      badge.textContent = review.checked ? '✓ Проверено для документа' : 'Проверьте перед переносом';
    });
  }
  function identity() {
    form.querySelectorAll('[data-identity-preview]').forEach(node => node.textContent = form.elements['identity_' + node.dataset.identityPreview].value || 'Не указано');
    const birth = form.elements.identity_birth_date.value;
    const date = form.dataset.today.split('-').map(Number), born = birth.split('-').map(Number);
    let age = null;
    if (birth && birth <= form.dataset.today && born.length === 3) age = date[0] - born[0] - (date[1] < born[1] || date[1] === born[1] && date[2] < born[2] ? 1 : 0);
    form.querySelector('[data-extract-age]').textContent = age === null ? 'Нужна дата рождения' : String(age);
  }
  form.querySelector('[data-confirm-extract]').addEventListener('click', () => {review.checked = true; badges();});
  function changed(event) {
    if (event.target !== review && event.target.matches('input,select,textarea')) review.checked = false;
    identity(); badges();
  }
  form.addEventListener('input', changed);
  form.addEventListener('change', changed);
  badges(); identity();
})();
