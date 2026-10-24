/* Status only: no patient input collection and no automatic model invocation. */
(() => {
  const panel = document.querySelector('#experiment-progress');
  if (!panel || !['queued', 'running'].includes(panel.dataset.runStatus)) return;
  const statuses = {queued:'В очереди', running:'Выполняется', completed:'Завершён', error:'Ошибка'};
  async function refresh() {
    try {
      const response = await fetch(`/api/experimental-ai/runs/${panel.dataset.runId}`, {cache:'no-store'});
      if (!response.ok) throw Error();
      const run = await response.json();
      panel.querySelector('[data-run-stage]').textContent = run.stage_label;
      panel.querySelector('[data-run-status-label]').textContent = statuses[run.status];
      panel.querySelector('[data-run-heartbeat]').textContent = `Последняя связь с обработчиком: ${run.heartbeat_at || 'ещё не было'}${run.heartbeat_at ? ' (UTC)' : ''}`;
      const error = panel.querySelector('[data-run-error]');
      error.textContent = run.error_message;
      error.hidden = !run.error_message;
      if (['completed', 'error'].includes(run.status)) {
        panel.querySelector('[data-run-refresh]').hidden = false;
        return;
      }
    } catch {
      panel.querySelector('[data-run-heartbeat]').textContent = 'Связь с приложением прервана. Обновите страницу для проверки.';
    }
    setTimeout(refresh, 4000);
  }
  refresh();
})();
