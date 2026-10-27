const assert = require('node:assert/strict');
const {urgent} = require('../../app/static/safety_support.js');
for (const [values, expected] of [
  [{'1':1,'2':0,'6':0},false], [{'2':1,'3':1,'4':0,'5':0},false],
  [{'2':1,'4':1},true], [{'2':1,'5':1},true], [{'2':0,'4':1},false],
  [{'2':null,'4':1},false], [{'6':1,'7':0},false], [{'6':1,'7':null},false],
  [{'6':0,'7':1},false], [{'6':1,'7':1},true],
]) assert.equal(urgent(values), expected);
console.log('PASS: 10 immediate help trigger cases');
