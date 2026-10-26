(() => {
  document.querySelectorAll('[data-print-document]').forEach(button => button.addEventListener('click', () => window.print()));
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
