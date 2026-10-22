import assert from 'node:assert/strict'
import { test } from 'node:test'
import { isOmexHash, matchesVerificationFilter, OMEX_HASH_PATTERN, parseServerTimestamp, parseVerificationListing, verificationTypeOf } from '../app/utils/verification-listing.ts'

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

test('isOmexHash recognizes 32-character hex hashes flexibly', () => {
  assert.equal(isOmexHash(hash), true)
  assert.equal(isOmexHash(hash.toUpperCase()), true)
  assert.equal(isOmexHash(`  ${hash}  `), true)
  assert.equal(isOmexHash(''), false)
  assert.equal(isOmexHash(null), false)
  assert.equal(isOmexHash(undefined), false)
  assert.equal(isOmexHash('omex-verification-12345'), false)
  assert.equal(isOmexHash(hash.slice(1)), false)
  assert.equal(isOmexHash(`${hash}0`), false)
  assert.equal(isOmexHash('z'.repeat(32)), false)
})

test('matchesVerificationFilter filters by workflow_id or omex_hash', () => {
  const item1 = summary('omex-verification-abc123xyz', { omex_hash: '11112222333344445555666677778888', verify_type: 'omex' })
  const item2 = summary('runs-verification-def456uvw', { omex_hash: 'aaaabbbbccccddddeeeeffff00001111', verify_type: 'runs' })
  const itemWithoutHash = { workflow_id: 'runs-verification-nohash' }

  // Empty query matches all
  assert.equal(matchesVerificationFilter(item1, ''), true)
  assert.equal(matchesVerificationFilter(item2, '   '), true)

  // Filter by workflow ID (exact, substring, case-insensitive)
  assert.equal(matchesVerificationFilter(item1, 'omex-verification-abc123xyz'), true)
  assert.equal(matchesVerificationFilter(item1, 'ABC123'), true)
  assert.equal(matchesVerificationFilter(item2, 'def456'), true)
  assert.equal(matchesVerificationFilter(item1, 'def456'), false)

  // Filter by OMEX hash (exact, substring, case-insensitive)
  assert.equal(matchesVerificationFilter(item1, '11112222333344445555666677778888'), true)
  assert.equal(matchesVerificationFilter(item1, '11112222'), true)
  assert.equal(matchesVerificationFilter(item1, '33334444'), true)
  assert.equal(matchesVerificationFilter(item2, 'AAAABBBB'), true)
  assert.equal(matchesVerificationFilter(item2, '11112222333344445555666677778888'), false)

  // Handles missing hash gracefully
  assert.equal(matchesVerificationFilter(itemWithoutHash, 'nohash'), true)
  assert.equal(matchesVerificationFilter(itemWithoutHash, '11112222'), false)

  // Respects type filter
  assert.equal(matchesVerificationFilter(item1, '', 'omex'), true)
  assert.equal(matchesVerificationFilter(item1, '', 'runs'), false)
  assert.equal(matchesVerificationFilter(item2, '', 'runs'), true)
  assert.equal(matchesVerificationFilter(item2, '', 'omex'), false)
  assert.equal(matchesVerificationFilter(item1, 'abc123', 'runs'), false)
  assert.equal(matchesVerificationFilter(item1, 'abc123', 'omex'), true)
})
