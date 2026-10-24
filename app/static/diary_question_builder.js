/* Constructor preview has no named answer fields and never saves a response. */
(() => {
  const form = document.querySelector('[data-question-builder]');
  const preview = document.querySelector('[data-builder-preview]');
  if (!form || !preview) return;
  const input = (tag, type) => {
    const element = document.createElement(tag);
    if (type) element.type = type;
    element.setAttribute('aria-label', 'Пример ответа пациента');
    return element;
  };
  const update = () => {
    const kind = form.elements.kind.value;
    form.querySelectorAll('[data-builder-kinds]').forEach(box => {
      const active = box.dataset.builderKinds.split(' ').includes(kind);
      box.hidden = !active;
      box.querySelectorAll('input,textarea').forEach(field => { field.disabled = !active; });
    });
    const frequency = form.elements.frequency.value;
    const days = form.querySelector('[name="weekdays"]').closest('fieldset');
    days.hidden = frequency !== 'weekdays';
    days.querySelectorAll('input').forEach(field => { field.disabled = frequency !== 'weekdays'; });
    const interval = form.elements.interval;
    interval.closest('label').hidden = frequency !== 'interval';
    interval.disabled = frequency !== 'interval';
    form.elements.start.required = frequency === 'interval';
    document.querySelector('[data-builder-preview-title]').textContent = form.elements.title.value || 'Ваш вопрос';
    preview.replaceChildren();
    const options = form.elements.options.value.split('\n').map(v => v.trim()).filter(Boolean);
    let control;
    if (kind === 'text') control = input('textarea');
    else if (['boolean', 'choice'].includes(kind)) {
      control = input('select');
      const values = kind === 'boolean' ? ['Выберите ответ', 'Да', 'Нет'] : ['Выберите ответ', ...options];
      values.forEach(text => { const option = document.createElement('option'); option.textContent = text; control.append(option); });
    } else if (kind === 'multi') {
      control = document.createElement('fieldset');
      const legend = document.createElement('legend'); legend.textContent = 'Можно выбрать несколько вариантов'; control.append(legend);
      options.forEach(text => { const label = document.createElement('label'); label.className = 'check'; const checkbox = input('input', 'checkbox'); checkbox.setAttribute('aria-label', text); label.append(checkbox, document.createTextNode(text)); control.append(label); });
    } else {
      const type = kind === 'scale' ? 'range' : ['file','voice'].includes(kind) ? 'file' : ['date','time'].includes(kind) ? kind : 'number';
      control = input('input', type);
      if (kind === 'scale') { control.min = form.elements.minimum.value || '0'; control.max = form.elements.maximum.value || '10'; control.step = '1'; }
      if (kind === 'number') { control.step = 'any'; if (form.elements.minimum.value) control.min = form.elements.minimum.value; if (form.elements.maximum.value) control.max = form.elements.maximum.value; }
      if (kind === 'duration') { control.min = '0'; control.max = '10080'; control.step = 'any'; }
      if (kind === 'voice') control.accept = '.wav,.mp3,.ogg,.webm,.m4a,.flac';
      if (kind === 'file') control.accept = '.pdf,.jpg,.jpeg,.png';
    }
    preview.append(control);
    if (kind === 'scale') {
      const caption = document.createElement('p');
      caption.textContent = `${control.min} ${form.elements.minimum_label.value} — ${control.max} ${form.elements.maximum_label.value}`;
      preview.append(caption);
    }
    if (['scale','number','duration'].includes(kind)) {
      const unit = document.createElement('p'); unit.textContent = kind === 'duration' ? 'Минуты' : form.elements.unit.value; preview.append(unit);
    }
  };
  form.addEventListener('input', update);
  form.addEventListener('change', update);
  update();
})();
