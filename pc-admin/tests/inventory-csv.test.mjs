import assert from 'node:assert/strict'
import test from 'node:test'
import { csvCell, csvTable } from '../src/views/inventory/csv.mjs'

test('CSV escapes quotes and formulas in user-entered inventory notes', () => {
  assert.equal(csvCell('报损"确认'), '"报损""确认"')
  assert.equal(csvCell('=HYPERLINK("x")'), '"\'=HYPERLINK(""x"")"')
  assert.equal(csvCell('-1'), '"\'-1"')
  assert.equal(csvTable([['单据', '说明'], ['IN-1', '@SUM(1)']]), '\uFEFF"单据","说明"\r\n"IN-1","\'@SUM(1)"\r\n')
})

test('CSV neutralizes formulas hidden behind whitespace, BOM or control characters', () => {
  for (const value of [' =1+1', '\uFEFF+SUM(1)', '\n@SUM(1)', '\ttext', '\rtext', '\u0000=1', ' \u001f-1']) {
    assert.equal(csvCell(value), `"'${value}"`, JSON.stringify(value))
  }
  assert.equal(csvCell(' ordinary text'), '" ordinary text"')
  assert.equal(csvCell(null), '""')
})
