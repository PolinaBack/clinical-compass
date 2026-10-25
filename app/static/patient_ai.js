/* Card calculations are read from the server; refreshing never starts a model job. */
(() => {
  function revealEvidence(hash) {
    if (!hash.startsWith('#av-')) return;
    const target = document.getElementById(hash.slice(1));
    if (!target) return;
    if (target.tagName === 'DETAILS') target.open = true;
    for (let parent = target.parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === 'DETAILS') parent.open = true;
    }
    return target;
  }
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href^="#av-"]');
    if (link?.closest('.aviandr-block')) revealEvidence(link.getAttribute('href'));
  });
  function revealCurrentHash() {
    const target = revealEvidence(location.hash);
    if (target) requestAnimationFrame(() => target.scrollIntoView({block: 'start'}));
  }
  window.addEventListener('hashchange', revealCurrentHash);
  revealCurrentHash();

  const pollingJobs = new WeakSet();
  function pollJobs(scope) {
    scope.querySelectorAll('[data-patient-ai]').forEach(panel => {
      if (pollingJobs.has(panel) || !['queued', 'running'].includes(panel.dataset.status)) return;
      pollingJobs.add(panel);
      async function refresh() {
        if (!panel.isConnected) return;
        try {
          const response = await fetch(panel.dataset.statusUrl, {cache: 'no-store'});
          if (!response.ok) throw Error();
          const job = await response.json();
          if (!panel.isConnected) return;
          panel.querySelector('[data-stage]').textContent = job.stage_label;
          panel.querySelector('[data-heartbeat]').textContent = `Связь с обработчиком: ${job.heartbeat_at || 'ещё не было'}${job.heartbeat_at ? ' (UTC)' : ''}`;
          const error = panel.querySelector('[data-error]');
          error.textContent = job.error_message;
          error.hidden = !job.error_message;
          if (['completed', 'error'].includes(job.status)) {
            panel.querySelector('[data-result-link]').hidden = false;
            panel.querySelectorAll('[data-job-pending]').forEach(item => item.hidden = true);
            const failure = panel.querySelector('[data-job-failure]');
            if (failure) failure.hidden = job.status !== 'error';
            return;
          }
        } catch {
          if (!panel.isConnected) return;
          panel.querySelector('[data-heartbeat]').textContent = 'Нет связи с приложением. Обновите страницу.';
        }
        setTimeout(refresh, 4000);
      }
      refresh();
    });
  }
  pollJobs(document);

  document.querySelectorAll('[data-aviandr-analysis][data-av-refresh-url]').forEach(block => {
    const status = block.querySelector('[data-av-live-status]');
    status.hidden = false;
    let pending = false, stopped = false, timer;
    function announce(message) {
      if (status.textContent !== message) status.textContent = message;
    }
    function applyUpdate(html, checksum) {
      const holder = document.createElement('template');
      holder.innerHTML = html;
      const freshBlock = holder.content.querySelector('[data-aviandr-analysis]');
      const fresh = freshBlock?.querySelector('[data-av-live]');
      const current = block.querySelector('[data-av-live]');
      if (!fresh || !current || freshBlock.dataset.avChecksum !== checksum) throw Error();
      const selection = window.getSelection();
      if (selection?.toString() && current.contains(selection.anchorNode)) return false;
      const detailsKey = node => node.id || node.querySelector('summary')?.textContent.trim();
      const opened = new Map([...current.querySelectorAll('details')].map(node => [detailsKey(node), node.open]));
      fresh.querySelectorAll('details').forEach(node => {
        const key = detailsKey(node);
        if (opened.has(key)) node.open = opened.get(key);
      });
      // Preserve the doctor's response format when only card facts refresh.
      const responseMode = current.querySelector('select[name="response_mode"]');
      const freshMode = fresh.querySelector('select[name="response_mode"]');
      if (responseMode && freshMode) freshMode.value = responseMode.value;
      const active = document.activeElement;
      const focus = current.contains(active) ? {
        tag: active.tagName, text: active.textContent.trim(), href: active.getAttribute('href'), id: active.id
      } : null;
      const anchor = [...current.querySelectorAll('[id]')]
        .filter(node => node.getClientRects().length && node.getBoundingClientRect().bottom > 0)
        .sort((a, b) => Math.abs(a.getBoundingClientRect().top) - Math.abs(b.getBoundingClientRect().top))[0];
      const anchorTop = anchor?.getBoundingClientRect().top;
      const below = current.getBoundingClientRect().bottom < 0;
      const oldHeight = current.getBoundingClientRect().height;
      // The editable clinician forms are deliberately outside data-av-live.
      current.replaceWith(fresh);
      block.dataset.avChecksum = checksum;
      block.dataset.avSelected = freshBlock.dataset.avSelected;
      if (focus) {
        const match = [...fresh.querySelectorAll('a,button,summary,select')].find(node =>
          focus.id ? node.id === focus.id : node.tagName === focus.tag &&
            node.getAttribute('href') === focus.href && node.textContent.trim() === focus.text
        );
        match?.focus({preventScroll: true});
      }
      if (anchor) {
        const replacement = document.getElementById(anchor.id);
        if (replacement) window.scrollBy(0, replacement.getBoundingClientRect().top - anchorTop);
      } else if (below) {
        window.scrollBy(0, fresh.getBoundingClientRect().height - oldHeight);
      }
      revealEvidence(location.hash);
      pollJobs(fresh);
      return true;
    }
    async function refresh() {
      clearTimeout(timer);
      if (pending || stopped || !block.isConnected) return;
      if (document.hidden) {
        timer = setTimeout(refresh, 5000);
        return;
      }
      pending = true;
      try {
        const url = new URL(block.dataset.avRefreshUrl, location.origin);
        url.searchParams.set('checksum', block.dataset.avChecksum);
        if (block.dataset.avSelected) url.searchParams.set('aviandr_id', block.dataset.avSelected);
        const response = await fetch(url, {cache: 'no-store', credentials: 'same-origin'});
        if ([401, 403, 404].includes(response.status)) {
          stopped = true;
          announce('Автообновление остановлено: проверьте вход и доступ к этой карте.');
          return;
        }
        if (!response.ok) throw Error();
        const result = await response.json();
        if (result.changed) {
          if (applyUpdate(result.html, result.checksum)) {
            announce('Карточки обновлены по новым сохранённым данным.');
          } else {
            announce('Есть новые данные. Обновим карточки после завершения выделения текста.');
          }
        } else if (status.textContent.includes('Не удалось')) {
          announce('Связь восстановлена. Карточки соответствуют сохранённым данным.');
        }
      } catch {
        announce('Не удалось проверить обновления. Данные экрана могли устареть; повторим автоматически.');
      } finally {
        pending = false;
        if (!stopped && block.isConnected) timer = setTimeout(refresh, 5000);
      }
    }
    window.addEventListener('focus', refresh);
    window.addEventListener('pageshow', refresh);
    document.addEventListener('visibilitychange', refresh);
    refresh();
  });
})();
