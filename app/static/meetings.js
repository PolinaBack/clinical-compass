const transcriptSearch = (() => {
  const fold = text => text.toLowerCase().replace(/ё/g, 'е').replace(/\s+/g, ' ').trim();
  const build = texts => {
    const refs = [];
    let text = '';
    const space = ref => {
      if (text && !text.endsWith(' ')) { text += ' '; refs.push(ref); }
    };
    texts.forEach((source, entryIndex) => {
      if (entryIndex) space(null);
      let offset = 0;
      for (const character of source) {
        const ref = {entryIndex, offset, end:offset + character.length};
        if (/\s/.test(character)) space(ref);
        else {
          const lowered = character.toLowerCase().replace(/ё/g, 'е');
          text += lowered;
          for (let i = 0; i < lowered.length; i++) refs.push(ref);
        }
        offset += character.length;
      }
    });
    return {texts, refs, text};
  };
  const find = (index, value) => {
    const phrase = fold(value), matches = [];
    if (phrase) {
      for (let start = index.text.indexOf(phrase); start !== -1; start = index.text.indexOf(phrase, start + phrase.length)) {
        matches.push(start);
      }
    }
    return matches;
  };
  const ranges = (index, position, value) => {
    const first = index.refs[position], last = index.refs[position + fold(value).length - 1];
    return index.texts.slice(first.entryIndex, last.entryIndex + 1).map((text, offset) => {
      const entryIndex = first.entryIndex + offset;
      return {text, entryIndex, start:entryIndex === first.entryIndex ? first.offset : 0,
        end:entryIndex === last.entryIndex ? last.end : text.length};
    });
  };
  return Object.freeze({fold, build, find, ranges});
})();
if (typeof module !== 'undefined' && module.exports) module.exports = transcriptSearch;

if (typeof document !== 'undefined') {
const openMeetingAncestors = element => {
  for (let node = element; node; node = node.parentElement) {
    if (node.matches('details')) node.open = true;
  }
};
const revealMeetingHash = () => {
  let id;
  try { id = decodeURIComponent(location.hash.slice(1)); }
  catch (_) { return; }
  const target = document.getElementById(id);
  if (!target?.closest('.meeting-page')) return;
  openMeetingAncestors(target);
  requestAnimationFrame(() => target.scrollIntoView({block:'start'}));
};
revealMeetingHash();
window.addEventListener('hashchange', revealMeetingHash);

document.querySelectorAll('[data-transcript-search]').forEach(root => {
  const tools = root.querySelector('[data-transcript-tools]');
  if (!tools) return;
  const query = tools.querySelector('[data-transcript-query]');
  const status = tools.querySelector('[data-transcript-results]');
  const snippet = tools.querySelector('[data-transcript-snippet]');
  const previous = tools.querySelector('[data-transcript-prev]');
  const next = tools.querySelector('[data-transcript-next]');
  const clear = tools.querySelector('[data-transcript-clear]');
  const nodes = Array.from(root.querySelectorAll('[data-transcript-text]'));
  const fold = transcriptSearch.fold;
  let index = null, matches = [], active = 0;

  const makeIndex = () => transcriptSearch.build(nodes.map(node => node.matches('textarea') ? node.value : node.textContent));
  const removeHighlight = () => {
    root.querySelectorAll('.meeting-search-hit').forEach(node => node.classList.remove('meeting-search-hit'));
    root.querySelectorAll('[data-transcript-text] mark').forEach(mark => mark.replaceWith(document.createTextNode(mark.textContent)));
  };
  const ranges = match => transcriptSearch.ranges(index, match, query.value).map(part => ({...part, node:nodes[part.entryIndex]}));
  const show = (jump = false) => {
    removeHighlight();
    previous.disabled = next.disabled = !matches.length;
    clear.disabled = !query.value;
    snippet.hidden = !matches.length;
    snippet.replaceChildren();
    if (!fold(query.value)) {
      status.textContent = 'Введите фразу. Поиск без учёта регистра.';
      return;
    }
    if (!matches.length) { status.textContent = 'Совпадений не найдено.'; return; }
    const selected = ranges(matches[active]);
    const first = selected[0], last = selected[selected.length - 1];
    const label = first.node.closest('[data-transcript-segment]').dataset.transcriptLabel;
    status.textContent = `Совпадение ${active + 1} из ${matches.length} · ${label}`;
    const mark = document.createElement('mark');
    mark.textContent = selected.map(part => part.text.slice(part.start, part.end)).join(' ');
    snippet.append(document.createTextNode(first.text.slice(Math.max(0, first.start - 70), first.start)), mark,
      document.createTextNode(last.text.slice(last.end, last.end + 70)));
    selected.forEach(part => {
      part.node.closest('[data-transcript-segment]').classList.add('meeting-search-hit');
      if (part.node.matches('textarea')) return;
      const highlight = document.createElement('mark');
      highlight.textContent = part.text.slice(part.start, part.end);
      part.node.replaceChildren(document.createTextNode(part.text.slice(0, part.start)), highlight,
        document.createTextNode(part.text.slice(part.end)));
    });
    if (jump) {
      openMeetingAncestors(first.node);
      first.node.closest('[data-transcript-segment]').scrollIntoView({block:'center'});
      if (first.node.matches('textarea')) {
        first.node.focus({preventScroll:true});
        first.node.setSelectionRange(first.start, first.end);
      }
    }
  };
  const search = () => {
    index = index || makeIndex();
    matches = transcriptSearch.find(index, query.value);
    active = 0;
    show();
  };
  query.addEventListener('input', search);
  query.addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); if (matches.length) show(true); }
    if (event.key === 'Escape') { query.value = ''; search(); }
  });
  previous.addEventListener('click', () => { active = (active - 1 + matches.length) % matches.length; show(true); });
  next.addEventListener('click', () => { active = (active + 1) % matches.length; show(true); });
  clear.addEventListener('click', () => { query.value = ''; search(); query.focus(); });
  nodes.filter(node => node.matches('textarea')).forEach(node => node.addEventListener('input', () => {
    index = null;
    if (query.value) search();
  }));
  tools.hidden = false;
});

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
}
