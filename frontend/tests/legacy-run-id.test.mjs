import assert from 'node:assert/strict'
import { test } from 'node:test'
import { legacyRunIdSegment } from '../server/utils/legacy-run-id.ts'

test('run IDs pass through as one literal segment', () => {
  for (const id of ['67817a2e1f52f47f628af971', '6abbf3f642828c6328de49bf', 'legacy_run-1']) {
    assert.equal(legacyRunIdSegment(id), id)
  }
})

test('anything that is not a single plain segment is refused', () => {
  for (const id of [
    undefined, '', '.', '..', 'a.b', 'a/b', 'a%2Fb', '%2e%2e', '%2E%2E', 'a b', 'a?b', 'a#b', 'a\\b',
    'x'.repeat(65)
  ]) assert.equal(legacyRunIdSegment(id), null, String(id))
})
