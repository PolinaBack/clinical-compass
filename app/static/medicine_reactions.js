(() => {
  document.querySelectorAll('[data-reaction-editor]').forEach(editor => {
    const symptom = editor.querySelector('[data-reaction-symptom]');
    const custom = editor.querySelector('[data-reaction-custom]');
    const relation = editor.querySelector('[data-reaction-relation]');
    const update = () => {
      custom.hidden = symptom.value !== 'other';
      const input = custom.querySelector('input');
      input.disabled = custom.hidden; input.required = !custom.hidden;
      if (relation) editor.querySelector('[data-reaction-assessment]').required = relation.value !== 'not_assessed';
    };
    symptom.addEventListener('change', update); relation?.addEventListener('change', update); update();
  });
})();
