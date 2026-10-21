import assert from 'node:assert/strict'
import { test } from 'node:test'
import { OMEX_HASH_PATTERN, parseServerTimestamp, parseVerificationListing, verificationTypeOf } from '../app/utils/verification-listing.ts'

const hash = '0123456789abcdef0123456789abcdef'

// The contract proposed on #136: verifications carries the same rows as verification_ids
const summary = (workflowId, fields = {}) => ({
  workflow_id: workflowId,
  verify_type: 'omex',
  created: '2026-10-21T19:28:55.215799Z',
  omex_hash: hash,
  status: null,
  ...fields
})

test('verifications rows are used as returned, in server order', () => {
  const rows = [summary('omex-verification-2', { status: 'COMPLETED' }), summary('runs-verification-1', { verify_type: 'runs' })]
  const page = parseVerificationListing({ verification_ids: rows.map(r => r.workflow_id), verifications: rows, next_cursor: 'c2' })
  assert.deepEqual(page, { rows, nextCursor: 'c2', summaries: true })
})

test('an archive filter is left to the server rather than re-applied in the browser', () => {
  const rows = [summary('omex-verification-2')]
  const page = parseVerificationListing({ verification_ids: ['omex-verification-2'], verifications: rows }, hash)
  assert.deepEqual(page, { rows, nextCursor: null, summaries: true })
})

test('backends that only list IDs still populate an unfiltered list', () => {
  const page = parseVerificationListing({ verification_ids: ['a', 'b'], next_cursor: 'c1' })
  assert.deepEqual(page, { rows: [{ workflow_id: 'a' }, { workflow_id: 'b' }], nextCursor: 'c1', summaries: false })
})

test('backends that ignore omex_hash never pass unrelated runs off as the archive\'s', () => {
  const page = parseVerificationListing({ verification_ids: ['a', 'b'], next_cursor: 'c1' }, hash)
  // summaries: false lets the page say the server can't filter, rather than that the archive has none
  assert.deepEqual(page, { rows: [], nextCursor: null, summaries: false })
})

test('the server\'s verify_type wins; bare IDs fall back to the default prefixes', () => {
  assert.equal(verificationTypeOf(summary('omex-verification-1', { verify_type: 'runs' })), 'runs')
  assert.equal(verificationTypeOf({ workflow_id: 'omex-verification-1' }), 'omex')
  assert.equal(verificationTypeOf({ workflow_id: 'runs-verification-1' }), 'runs')
  for (const id of ['my-custom-prefix-1', 'omex-1', 'runs-1']) {
    assert.equal(verificationTypeOf({ workflow_id: id }), null)
  }
})

test('the archive hash pattern matches /compatibility/check omex_id values only', () => {
  assert.equal(OMEX_HASH_PATTERN.test(hash), true)
  for (const value of ['', hash.toUpperCase(), hash.slice(1), `${hash}0`, 'z'.repeat(32)]) {
    assert.equal(OMEX_HASH_PATTERN.test(value), false)
  }
})

test('server timestamps without an offset are read as UTC', () => {
  assert.equal(parseServerTimestamp('2026-10-21T19:28:55.215799').toISOString(), '2026-10-21T19:28:55.215Z')
  assert.equal(parseServerTimestamp('2026-10-21T19:28:55Z').toISOString(), '2026-10-21T19:28:55.000Z')
  assert.equal(parseServerTimestamp('2026-10-21T21:28:55+02:00').toISOString(), '2026-10-21T19:28:55.000Z')
})
