/* Presentation only. Native radios and POST forms remain usable without this file. */
(() => {
  document.querySelectorAll('[data-questionnaire-answer]').forEach(group => {
    group.addEventListener('change', event => {
      if (!event.target.matches('input[type="radio"]')) return;
      const badge = group.closest('[data-questionnaire-question]')?.querySelector('[data-questionnaire-state]');
      if (!badge) return;
      const value = event.target.value;
      const labels = {unanswered:'Нет ответа', deferred:'Отложено', declined:'Не готов(а) отвечать'};
      badge.textContent = labels[value] || 'Ответ выбран';
      badge.dataset.answerState = labels[value] ? value : 'answered';
    });
  });
  document.querySelectorAll('[data-questionnaire-assignment]').forEach(details => details.open = false);
  document.querySelectorAll('.questionnaire-page form').forEach(form => {
    form.addEventListener('invalid', event => {
      for (let node=event.target.parentElement; node; node=node.parentElement) {
        if (node.tagName === 'DETAILS') node.open = true;
      }
    }, true);
  });
})();
