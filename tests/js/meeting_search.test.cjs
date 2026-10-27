const test = require('node:test');
const assert = require('node:assert/strict');
const search = require('../../app/static/meetings.js');

const selections = (texts, phrase) => {
  const index = search.build(texts);
  return search.find(index, phrase).map(position => search.ranges(index, position, phrase));
};

test('finds all phrases ignoring case, whitespace, and е/ё without changing the text', () => {
  const texts = ['  Сон стал\nлучше. Ещё одна тема.', 'СОН   СТАЛ лучше.'];
  const original = [...texts];
  const results = selections(texts, 'сон  СТАЛ лучше');
  assert.equal(results.length, 2);
  assert.equal(results[0][0].text.slice(results[0][0].start, results[0][0].end), 'Сон стал\nлучше');
  assert.equal(results[1][0].entryIndex, 1);
  assert.equal(selections(texts, 'еще одна тема').length, 1);
  assert.deepEqual(texts, original);
});

test('finds a phrase spanning consecutive fragments and maps both selection ranges', () => {
  const results = selections(['Вступление. Переход к', 'следующему вопросу. Завершение.'], 'переход к следующему вопросу');
  assert.equal(results.length, 1);
  assert.equal(results[0].length, 2);
  assert.deepEqual(results[0].map(part => part.text.slice(part.start, part.end)), ['Переход к', 'следующему вопросу']);
});

test('treats regular expression and HTML characters as literal search text', () => {
  const text = 'Доза (1,5 мг) [утро]. <script>test()</script> a+b?';
  for (const phrase of ['(1,5 мг)', '[утро]', '<script>test()</script>', 'a+b?']) {
    const results = selections([text], phrase);
    assert.equal(results.length, 1);
    const range = results[0][0];
    assert.equal(range.text.slice(range.start, range.end), phrase);
  }
});

test('uses original UTF-16 offsets for selecting emoji and Unicode case variants', () => {
  for (const phrase of ['СОН СТАЛ', '🧭', '𐐨']) {
    const results = selections(['🧭 Сон стал лучше. 𐐀'], phrase);
    assert.equal(results.length, 1);
    const range = results[0][0];
    assert.equal(search.fold(range.text.slice(range.start, range.end)), search.fold(phrase));
  }
});

test('returns no matches for an empty or absent phrase', () => {
  for (const texts of [[], [''], ['Только одна тема.']]) {
    for (const phrase of ['', '  \n ', 'Нет такого текста']) {
      assert.deepEqual(selections(texts, phrase), []);
    }
  }
});

test('rebuilding from an edited transcript reflects unsaved corrections', () => {
  const texts = ['Сон стал лучше.', 'Сон стал лучше.'];
  assert.equal(selections(texts, 'сон стал лучше').length, 2);
  texts[0] = 'Сон стал иначе.';
  assert.equal(selections(texts, 'сон стал лучше').length, 1);
  assert.equal(selections(texts, 'сон стал иначе')[0][0].entryIndex, 0);
});
