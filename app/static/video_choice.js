/* A refusal never submits the current question or discards unsaved answers. */
(() => {
  const form = document.querySelector('form[data-video-choice]');
  if (!form) return;
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button');
    button.disabled = true;
    let message = form.closest('.video-offer').querySelector('[data-video-choice-status]');
    if (!message) {
      message = document.createElement('p'); message.className = 'caption';
      message.setAttribute('role','status'); message.dataset.videoChoiceStatus = '';
      form.parentElement.after(message);
    }
    try {
      const response = await fetch(form.action,{method:'POST',body:new FormData(form),headers:{Accept:'application/json'}});
      if (!response.ok) throw new Error();
      message.textContent = 'Вы выбрали прохождение без видеозаписи. Решение можно изменить.';
      form.hidden = true;
    } catch {
      button.disabled = false;
      message.textContent = 'Не удалось сохранить выбор. Можно продолжить анкету и повторить позже.';
    }
  });
})();
