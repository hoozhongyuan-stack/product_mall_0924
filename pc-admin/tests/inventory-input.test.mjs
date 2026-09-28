import assert from 'node:assert/strict'
import test from 'node:test'
import { parsePositiveQuantity } from '../src/views/inventory/quantity.mjs'

test('inbound quantity accepts only safe positive integer operation units', () => {
  assert.equal(parsePositiveQuantity('12'), 12)
  assert.equal(parsePositiveQuantity(' 0012 '), 12)
  for (const invalid of ['', '0', '-2', '1.5', '1e3', '9007199254740992']) {
    assert.equal(parsePositiveQuantity(invalid), null, invalid)
  }
})
