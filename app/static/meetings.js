document.querySelectorAll('[data-meeting-seek]').forEach(button => {
  button.addEventListener('click', async () => {
    const media = document.getElementById(button.dataset.media);
    if (!media) return;
    const time = Number(button.dataset.meetingSeek);
    if (!Number.isFinite(time) || time < 0) return;
    if (media.readyState === 0) {
      media.load();
      await new Promise(resolve => {
        media.addEventListener('loadedmetadata', resolve, {once:true});
        media.addEventListener('error', resolve, {once:true});
      });
    }
    try { media.currentTime = time; media.focus(); media.scrollIntoView({block:'center'}); }
    catch (_) { /* Native controls remain available if media cannot be decoded. */ }
  });
});

const meeting = document.querySelector('[data-meeting-jobs-url]');
if (meeting && meeting.querySelector('[data-meeting-job]')) {
  let fetching = false;
  const setText = (box, name, value) => {
    const node = box.querySelector(`[data-job-${name}]`);
    if (node && node.textContent !== (value || '')) node.textContent = value || '';
    return node;
  };
  const update = async () => {
    if (document.hidden || fetching) return;
    fetching = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(meeting.dataset.meetingJobsUrl, {cache:'no-store', credentials:'same-origin', signal:controller.signal});
      if (!response.ok) throw new Error('status-unavailable');
      const data = await response.json();
      for (const job of data.jobs) {
        const box = meeting.querySelector(`[data-meeting-job="${job.id}"]`);
        if (!box) continue;
        box.dataset.jobState = job.status;
        setText(box, 'status', `Статус: ${job.status_label} · ${job.stage_label}`);
        setText(box, 'progress', job.progress_text);
        setText(box, 'elapsed', job.elapsed_text);
        setText(box, 'activity', job.activity_text);
        const warning = setText(box, 'warning', job.warning);
        warning.hidden = !job.warning;
        const error = setText(box, 'error', job.error_message + (job.status === 'failed' ? ' После исправления нажмите кнопку обработки ещё раз.' : ''));
        error.hidden = !job.error_message;
        const bar = box.querySelector('progress');
        bar.hidden = ['failed','cancelled'].includes(job.status);
        if (job.percent === null) bar.removeAttribute('value');
        else bar.value = job.percent;
        box.querySelector('[data-job-ready]').hidden = job.status !== 'done';
      }
      const available = new Set(data.jobs.map(job => String(job.id)));
      meeting.querySelectorAll('[data-meeting-job]').forEach(box => {
        if (!available.has(box.dataset.meetingJob)) {
          setText(box, 'activity', '');
          setText(box, 'status', 'Статус недоступен: запись удалена или срок хранения истёк.');
          box.querySelector('progress').hidden = true;
          box.querySelector('[data-job-ready]').hidden = true;
        }
      });
      meeting.querySelectorAll('[data-job-poll-status]').forEach(node => {
        const message = 'Статус проверен. Обновление каждые 5 секунд; текст и формы остаются на месте.';
        if (node.textContent !== message) node.textContent = message;
      });
    } catch (_) {
      meeting.querySelectorAll('[data-job-poll-status]').forEach(node => {
        const message = 'Не удалось обновить статус. Показаны прежние данные; проверьте связь с приложением.';
        if (node.textContent !== message) node.textContent = message;
      });
    } finally {
      clearTimeout(timeout);
      fetching = false;
    }
  };
  update();
  setInterval(update, 5000);
  document.addEventListener('visibilitychange', update);
}
