// Exercise the shipped polling script without network or a browser installation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function check(mode) {
  const calls = [], fields = {
    '[data-stage]': {}, '[data-heartbeat]': {}, '[data-error]': {},
    '[data-result-link]': {hidden: true}, '[data-job-failure]': {hidden: true},
  };
  let replaced = false;
  const target = '/api/patients/1/ai-analysis/1';
  const replacement = {querySelectorAll: () => [fresh]};
  const fresh = {
    dataset: {status: 'completed', statusUrl: mode === 'wrong-job' ? target + '2' : target},
    closest: () => replacement,
  };
  const area = {isConnected: true, replaceWith(node) {assert.equal(node, replacement); replaced = true;}};
  const panel = {
    isConnected: true,
    dataset: {status: 'running', statusUrl: target, resultUrl: '/patients/1/ai-analysis?run_id=1'},
    querySelector: selector => fields[selector], querySelectorAll: () => [], closest: () => area,
  };
  const document = {
    querySelectorAll: selector => selector === '[data-patient-ai]' ? [panel] : [],
    addEventListener() {},
    createElement: tag => {assert.equal(tag, 'template'); return {content: {querySelectorAll: () => [fresh]}};},
  };
  vm.runInNewContext(fs.readFileSync('app/static/patient_ai.js', 'utf8'), {
    document, window: {addEventListener() {}}, location: {hash: ''}, setTimeout() {}, clearTimeout() {},
    fetch: async (url, options) => {
      calls.push({url, options});
      if (url === target) return {ok: true, json: async () => ({status: 'completed', stage_label: 'Завершён', heartbeat_at: '', error_message: ''})};
      return {ok: mode !== 'failed-page', text: async () => '<div>Test result area</div>'};
    },
  });
  await new Promise(setImmediate);
  assert.equal(calls.length, 2);
  assert.equal(calls[1].options.credentials, 'same-origin');
  assert.equal(fields['[data-result-link]'].hidden, false);
  assert.equal(replaced, mode === 'completed');
}

(async () => {
  for (const mode of ['completed', 'wrong-job', 'failed-page']) await check(mode);
  console.log('PASS: completed response replaces its result area automatically; another job is never inserted; failed page fetch keeps manual refresh available.');
})().catch(error => {console.error(error); process.exitCode = 1;});
