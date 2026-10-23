/* Leaf dialogs enhance ordinary links; with no JS the same forms have their own page. */
(() => {
  const add = document.getElementById('family-add-dialog');
  const edit = document.getElementById('family-edit-dialog');
  if (!add || !edit || typeof add.showModal !== 'function') return;
  const people = JSON.parse(document.getElementById('family-people').textContent);
  const addForm = add.querySelector('form');
  const editForm = edit.querySelector('form');
  let opener;
  const garden = document.querySelector('.family-patient-scroll');
  if (garden && !location.hash.startsWith('#leaf-')) garden.scrollLeft = (garden.scrollWidth - garden.clientWidth) / 2;
  const field = (form, name) => form.elements.namedItem(name);
  function customField() {
    const enabled = field(addForm, 'relation').value === 'custom';
    const label = addForm.querySelector('[data-custom-relation]');
    label.hidden = !enabled;
    field(addForm, 'custom_relation').disabled = !enabled;
    field(addForm, 'custom_relation').required = enabled;
  }
  field(addForm, 'relation').addEventListener('change', customField);
  document.querySelectorAll('[data-family-add], [data-family-edit]').forEach(link => {
    link.setAttribute('aria-haspopup', 'dialog');
    link.addEventListener('click', event => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0) return;
      event.preventDefault();
      opener = link;
      if (link.hasAttribute('data-family-add')) {
        addForm.reset();
        field(addForm, 'relation').value = link.dataset.familyAdd;
        customField();
        add.querySelector('details').open = false;
        add.showModal();
        field(addForm, link.dataset.familyAdd ? 'name' : 'relation').focus();
      } else {
        const key = link.dataset.familyEdit;
        const person = people[key];
        if (!person) { location.href = link.href; return; }
        editForm.action = link.href.replace('/profile/family/member/', '/family/');
        for (const name of ['name', 'about', 'sex', 'age']) field(editForm, name).value = person[name] ?? '';
        field(editForm, 'deceased').checked = Boolean(person.deceased);
        const custom = field(editForm, 'custom_relation');
        edit.querySelector('[data-edit-custom]').hidden = person.custom_relation === null;
        custom.disabled = person.custom_relation === null;
        custom.required = person.custom_relation !== null;
        custom.value = person.custom_relation ?? '';
        edit.querySelector('[data-family-full]').href = link.href;
        edit.querySelector('details').open = false;
        document.getElementById('family-edit-title').textContent = person.label;
        edit.showModal();
        field(editForm, 'name').focus();
      }
    });
  });
  for (const dialog of [add, edit]) {
    dialog.querySelector('[data-family-close]').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => { if (opener?.isConnected) opener.focus({preventScroll:true}); });
    dialog.addEventListener('keydown', event => {
      if (event.key !== 'Tab') return;
      const controls = [...dialog.querySelectorAll('a[href],button,input,select,textarea,summary,[tabindex]')]
        .filter(control => !control.disabled && control.tabIndex >= 0 && control.getClientRects().length);
      const first = controls[0], last = controls[controls.length - 1];
      if (!first) return;
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus();
      }
    });
  }
})();
