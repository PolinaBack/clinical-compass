(() => {
  const input = document.querySelector('#medicine-query');
  if (!input) return;
  const list = document.querySelector('#medicine-suggestions');
  const status = document.querySelector('#medicine-suggestion-status');
  let timer, controller;
  input.addEventListener('input', () => {
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
})();
