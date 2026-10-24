(() => {
  const editor = document.getElementById('calendar-editor');
  const form = document.getElementById('calendar-event-form');
  if (!editor || !form) return;
  document.querySelectorAll('[data-calendar-submit]').forEach(el => el.addEventListener('change', () => el.form.requestSubmit()));
  const open = (value, past = false) => {
    const [day, time] = value.split('T');
    if (day) form.elements.date.value = day;
    if (time) form.elements.time.value = time;
    const connection = form.querySelector('[data-calendar-connection]');
    connection.open = !past && (!form.elements.url.value || !form.elements.fallback.value);
    editor.open = true;
    editor.scrollIntoView({block: 'center', behavior: 'smooth'});
    form.elements.time.focus({preventScroll: true});
  };
  document.querySelectorAll('[data-calendar-time]').forEach(button => button.addEventListener('click', () => open(button.dataset.calendarTime)));
  document.querySelector('[data-calendar-add]')?.addEventListener('click', () => open(form.elements.date.value + 'T' + form.elements.time.value));
  document.querySelector('[data-calendar-past]')?.addEventListener('click', () => {
    // A date/time is only selected here; the server validates the actual instant.
    const value = new Date(); value.setDate(value.getDate()-1);
    const day = [value.getFullYear(), String(value.getMonth()+1).padStart(2,'0'), String(value.getDate()).padStart(2,'0')].join('-');
    const selected = document.querySelector('.calendar-patient-picker select');
    if (selected?.value) form.elements.patient_id.value = selected.value;
    open(day + 'T10:00', true);
  });
  document.querySelector('[data-calendar-close]')?.addEventListener('click', () => { editor.open = false; });
  document.querySelectorAll('[data-calendar-slot]').forEach(button => button.addEventListener('click', () => {
    const panel = document.getElementById('calendar-slot-details');
    panel.hidden = false;
    panel.querySelector('[data-slot-heading]').textContent = button.dataset.slotLabel;
    panel.querySelector('[data-slot-cancel]').action = '/doctor/calendar/slots/' + button.dataset.calendarSlot + '/cancel';
    const book = panel.querySelector('[data-slot-book]');
    book.elements.slot_id.value = button.dataset.calendarSlot;
    const select = document.querySelector('.calendar-patient-picker select');
    if (select?.value) book.elements.calendar_patient.value = select.value;
    panel.scrollIntoView({block:'center',behavior:'smooth'});
  }));
  document.querySelector('[data-slot-book]')?.addEventListener('submit', event => {
    const booking = event.target;
    booking.action = '/patients/' + booking.elements.calendar_patient.value + '/consultations';
    // Keep the chooser usable if the browser returns after a stale-slot error.
    // The server scopes the actual booking to the patient ID in the route.
  });
  const scroll = document.querySelector('[data-calendar-scroll]');
  if (scroll) scroll.scrollTop = 7 * 60;
})();
