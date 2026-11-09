import assert from 'node:assert/strict'
import { test } from 'node:test'
import { clock, fmt, opLabel } from '../src/format.ts'
import type { Operation } from '../src/types.ts'

test('fmt rounds to 3 decimals and drops trailing zeros', () => {
  assert.deepEqual([6.8, 12, 1 / 3, 0.1 + 0.2].map(fmt), ['6.8', '12', '0.333', '0.3'])
})

test('clock writes whole seconds as m:ss', () => {
  assert.deepEqual([0, 9.9, 60, 61.5, 3599].map(clock), ['0:00', '0:09', '1:00', '1:01', '59:59'])
})

test('opLabel shows the spot depth, or the cycle with its peck', () => {
  const op = (o: Partial<Operation>): Operation =>
    ({ op: 'drill', tool_id: 'DRL-6.8', hole_ids: [1, 2], depth: 0, cycle: 'G81', peck: 0, reason: '', ...o })
  const numbers = { 'SPOT-10': 1, 'DRL-6.8': 3 }
  assert.equal(opLabel(op({ op: 'spot_drill', tool_id: 'SPOT-10', depth: 1.5 }), numbers), 'T1 Spot drill · 1.5 mm deep · holes 1, 2')
  assert.equal(opLabel(op({ cycle: 'G83', peck: 2 }), numbers), 'T3 Drill · G83 peck 2 · holes 1, 2')
  assert.equal(opLabel(op({}), numbers), 'T3 Drill · G81 · holes 1, 2')
})
