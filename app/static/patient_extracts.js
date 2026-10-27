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
  const diagnosis = form.querySelector('[data-diagnosis-picker]');
  const entries = [...diagnosis.querySelectorAll('[data-diagnosis-code]')];
  const normalize = value => value.trim().toLocaleLowerCase('ru').replaceAll('ё','е');
  function filterDiagnoses() {
    const group = diagnosis.querySelector('[data-diagnosis-group]').value;
    const terms = normalize(diagnosis.querySelector('[data-diagnosis-query]').value).split(/[\s,;]+/);
    entries.forEach(entry => entry.hidden = Boolean(group && entry.dataset.diagnosisGroupKey !== group || !terms.every(term => normalize(entry.dataset.diagnosisText).includes(term))));
    diagnosis.querySelector('[data-diagnosis-count]').textContent = 'Найдено: ' + entries.filter(entry => !entry.hidden).length;
  }
  function selectedDiagnosis() {
    const code = form.elements.diagnosis_code.value.trim(), text = form.elements.diagnosis.value.trim();
    diagnosis.querySelector('[data-selected-diagnosis]').textContent = [code,text].filter(Boolean).join(' · ') || 'Формулировка не выбрана';
    entries.forEach(entry => entry.setAttribute('aria-pressed', String(entry.dataset.diagnosisCode === code && entry.dataset.diagnosisName === text)));
  }
  diagnosis.querySelector('[data-diagnosis-group]').addEventListener('change', filterDiagnoses);
  diagnosis.querySelector('[data-diagnosis-query]').addEventListener('input', filterDiagnoses);
  diagnosis.querySelector('[data-diagnosis-query]').addEventListener('keydown', event => {if(event.key==='Enter'){event.preventDefault();filterDiagnoses();}});
  diagnosis.addEventListener('click', event => {
    const option = event.target.closest('[data-diagnosis-code]'), clear = event.target.closest('[data-clear-diagnosis]'), search = event.target.closest('[data-diagnosis-search]');
    if (search) {event.preventDefault();filterDiagnoses();return;}
    if (!option && !clear) return;
    event.preventDefault();
    form.elements.diagnosis_code.value = option?.dataset.diagnosisCode || '';
    form.elements.diagnosis.value = option?.dataset.diagnosisName || '';
    form.elements.diagnosis_status.value = 'working';
    form.elements.diagnosis.dispatchEvent(new Event('input', {bubbles:true}));
  });
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
    if (event.target !== review && event.target.matches('input,select,textarea') && !event.target.matches('[data-diagnosis-query],[data-diagnosis-group]')) review.checked = false;
    identity(); badges(); selectedDiagnosis();
  }
  form.addEventListener('input', changed);
  form.addEventListener('change', changed);
  badges(); identity(); selectedDiagnosis(); filterDiagnoses();
})();
