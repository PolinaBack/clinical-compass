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
            const area = panel.closest('[data-ai-result-area]');
            if (area && panel.dataset.resultUrl) {
              try {
                const resultPage = await fetch(panel.dataset.resultUrl, {cache: 'no-store', credentials: 'same-origin'});
                if (!resultPage.ok) throw Error();
                const holder = document.createElement('template');
                holder.innerHTML = await resultPage.text();
                const fresh = [...holder.content.querySelectorAll('[data-patient-ai]')]
                  .find(item => item.dataset.statusUrl === panel.dataset.statusUrl);
                const replacement = fresh?.closest('[data-ai-result-area]');
                if (!replacement || !area.isConnected) throw Error();
                area.replaceWith(replacement);
                pollJobs(replacement);
              } catch (_) { /* Manual result refresh remains available. */ }
            }
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

  function markReviewDirty(event) {
    const form = event.target.closest('[data-av-review-form]');
    if (!form) return;
    form.dataset.dirty = 'true';
    if (['decision','adult_confirmed','unknown_reviewed','covid_relation','mild_moderate'].includes(event.target.name)) {
      form.querySelector('[name=review_ack]').checked = false;
    }
  }
  document.addEventListener('input', markReviewDirty);
  document.addEventListener('change', markReviewDirty);
  document.querySelectorAll('[data-aviandr-analysis][data-av-refresh-url]').forEach(block => {
    const status = block.querySelector('[data-av-live-status]');
    status.hidden = false;
    let pending = false, stopped = false, timer, localRevision = 0;
    function announce(message) {
      if (status.textContent !== message) status.textContent = message;
    }
    // Save the current card without a navigation or a wait for the polling timer.
    // Other open confirmation forms and unsaved text remain in place.
    block.addEventListener('submit', async event => {
      const form = event.target.closest('[data-av-review-form]');
      if (!form || !window.fetch || !window.FormData) return;
      event.preventDefault();
      if (form.dataset.saving === 'true') return;
      const card = form.closest('[data-direction]');
      const key = card.dataset.direction;
      const button = form.querySelector('button');
      const label = button.textContent;
      const fields = new FormData(form);
      localRevision++;
      form.dataset.dirty = 'true';
      form.dataset.saving = 'true';
      button.disabled = true;
      button.textContent = 'Сохраняем подтверждение…';
      announce('Сохраняем проверку врача.');
      try {
        const response = await fetch(form.action, {
          method: 'POST', body: fields, credentials: 'same-origin', cache: 'no-store',
          headers: {Accept: 'application/json'}
        });
        if ([401, 403, 404].includes(response.status)) throw Error('access');
        let html;
        if (response.ok) html = (await response.json()).html;
        else if ([409, 422].includes(response.status)) html = await response.text();
        else throw Error();
        const holder = document.createElement('template');
        holder.innerHTML = html;
        const fresh = holder.content.querySelector(`[data-direction="${key}"]`);
        if (!fresh || !card.isConnected) throw Error();
        // Also invalidate polls that began while this POST was in flight.
        localRevision++;
        const before = card.getBoundingClientRect().top;
        card.replaceWith(fresh);
        const currentItem = block.querySelector(`.aviandr-status-chart a[href="#av-${key}"]`)?.closest('li');
        const freshItem = holder.content.querySelector(`.aviandr-status-chart a[href="#av-${key}"]`)?.closest('li');
        if (currentItem && freshItem) currentItem.replaceWith(freshItem);
        window.scrollBy(0, fresh.getBoundingClientRect().top - before);
        if (response.ok) {
          const message = `Проверка сохранена. ${fresh.querySelector('h3').textContent}: ${fresh.querySelector('.aviandr-status').textContent}.`;
          let feedback = fresh.querySelector('[data-av-save-feedback]');
          if (!feedback) {
            feedback = document.createElement('p');
            feedback.dataset.avSaveFeedback = '';
            feedback.className = 'aviandr-review-summary';
            feedback.setAttribute('role', 'status');
            fresh.append(feedback);
          }
          feedback.textContent = message;
          announce(message);
        } else {
          fresh.querySelector('[data-av-review-form]').dataset.dirty = 'true';
          const error = fresh.querySelector('[role=alert]');
          error?.setAttribute('tabindex', '-1');
          error?.focus({preventScroll: true});
          announce(error?.textContent || 'Проверьте сведения и повторите подтверждение.');
        }
        refresh();
      } catch (error) {
        announce(error.message === 'access'
          ? 'Подтверждение не сохранено: проверьте вход и доступ к карте.'
          : 'Не удалось получить подтверждение сохранения. Данные формы сохранены на экране; повторите отправку.');
      } finally {
        if (form.isConnected) {
          delete form.dataset.saving;
          button.disabled = false;
          button.textContent = label;
        }
      }
    });
    function applyUpdate(html, checksum) {
      const holder = document.createElement('template');
      holder.innerHTML = html;
      const freshBlock = holder.content.querySelector('[data-aviandr-analysis]');
      const fresh = freshBlock?.querySelector('[data-av-live]');
      const current = block.querySelector('[data-av-live]');
      if (!fresh || !current || freshBlock.dataset.avChecksum !== checksum) throw Error();
      const active = document.activeElement;
      const drafts = [...current.querySelectorAll('[data-av-review-form]')]
        .filter(form => form.dataset.dirty === 'true' || form.contains(active));
      const selection = window.getSelection();
      if (drafts.some(form => form.dataset.saving === 'true') || (selection?.toString() && current.contains(selection.anchorNode))) {
        // Preserve a selected passage, but never hide a newly discovered risk.
        fresh.querySelectorAll('[data-direction]').forEach(card => {
          const shown = current.querySelector(`[data-direction="${card.dataset.direction}"]`);
          if (shown) {
            shown.dataset.level = card.dataset.level;
            const label = shown.querySelector('.aviandr-status');
            if (label && !label.contains(selection?.anchorNode)) label.textContent = card.querySelector('.aviandr-status').textContent;
          }
        });
        const nav = current.querySelector('.aviandr-status-chart');
        const freshNav = fresh.querySelector('.aviandr-status-chart');
        if (nav && freshNav) nav.replaceWith(freshNav);
        return false;
      }
      const detailsKey = node => node.id || node.querySelector('summary')?.textContent.trim();
      const opened = new Map([...current.querySelectorAll('details')].map(node => [detailsKey(node), node.open]));
      fresh.querySelectorAll('details').forEach(node => {
        const key = detailsKey(node);
        if (opened.has(key)) node.open = opened.get(key);
      });
      const focus = current.contains(active) ? {
        tag: active.tagName, text: active.textContent.trim(), href: active.getAttribute('href'), id: active.id
      } : null;
      const anchor = [...current.querySelectorAll('[id]')]
        .filter(node => node.getClientRects().length && node.getBoundingClientRect().bottom > 0)
        .sort((a, b) => Math.abs(a.getBoundingClientRect().top) - Math.abs(b.getBoundingClientRect().top))[0];
      const anchorTop = anchor?.getBoundingClientRect().top;
      const below = current.getBoundingClientRect().bottom < 0;
      const oldHeight = current.getBoundingClientRect().height;
      const caret = active && typeof active.selectionStart === 'number'
        ? [active.selectionStart, active.selectionEnd, active.selectionDirection] : null;
      drafts.forEach(form => {
        const key = form.closest('[data-direction]').dataset.direction;
        const replacement = fresh.querySelector(`[data-direction="${key}"] [data-av-review-form]`);
        if (!replacement) throw Error();
        // Keep values, acknowledgement and the original snapshot. The server
        // must reject a confirmation submitted against changed relevant data.
        replacement.replaceWith(form);
        let notice = form.querySelector('[data-av-form-changed]');
        if (!notice) {
          notice = document.createElement('p');
          notice.dataset.avFormChanged = '';
          notice.className = 'aviandr-review-stale';
          notice.setAttribute('role', 'status');
          form.prepend(notice);
        }
        notice.textContent = 'Карточка обновлена по новым сведениям. Ваш текст сохранён; перед подтверждением проверьте изменения.';
      });
      // Generic clinician forms remain outside; in-card drafts move intact.
      current.replaceWith(fresh);
      block.dataset.avChecksum = checksum;
      block.dataset.avSelected = freshBlock.dataset.avSelected;
      if (drafts.some(form => form.contains(active)) && active.isConnected) {
        active.focus({preventScroll: true});
        if (caret && active.setSelectionRange) active.setSelectionRange(...caret);
      } else if (focus) {
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
      const revision = localRevision;
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
        if (revision !== localRevision) return;
        if (result.changed) {
          if (applyUpdate(result.html, result.checksum)) {
            announce('Карточки обновлены по новым сохранённым данным.');
          } else {
            announce('Цвета и статусы обновлены. Содержание обновится после завершения сохранения или снятия выделения текста.');
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
