import assert from 'node:assert/strict'
import test from 'node:test'
import { verificationIsDone, verificationIsSaving, verificationDisplayStatus, isUnavailableVerification } from '../app/utils/verification-status.ts'

test('terminal output keeps polling until durable storage acknowledges', () => {
  for (const workflow_status of ['COMPLETED', 'FAILED', 'RUN_ID_NOT_FOUND']) {
    assert.equal(verificationIsDone({ workflow_status, persistence_state: 'pending' }), false)
    assert.equal(verificationIsDone({ workflow_status, persistence_state: 'blocked' }), false)
    assert.equal(verificationIsDone({ workflow_status, persistence_state: 'stored' }), true)
    assert.equal(verificationIsDone({ workflow_status }), true)
  }
})
test('halted and expired lookups cannot masquerade as pending', () => {
  assert.equal(verificationDisplayStatus(null, true), 'UNAVAILABLE')
  assert.equal(verificationDisplayStatus(null, false), 'PENDING')
  assert.equal(isUnavailableVerification({ response: { status: 404 }, data: { detail: 'exists but its results are no longer retained.' } }), true)
  assert.equal(isUnavailableVerification({ response: { status: 503 } }), false)
  assert.equal(isUnavailableVerification(null), false)
})
test('only a computed result that is not stored yet is saving', () => {
  // Still computing: persistence_state is 'pending' by default, but nothing is being saved.
  assert.equal(verificationIsSaving({ workflow_status: 'IN_PROGRESS', persistence_state: 'pending' }), false)
  assert.equal(verificationIsSaving({ workflow_status: 'IN_PROGRESS', persistence_state: 'pending', report_available: null }), false)
  // The persist step is running.
  assert.equal(verificationIsSaving({ workflow_status: 'IN_PROGRESS', persistence_state: 'pending', report_available: false }), true)
  for (const workflow_status of ['COMPLETED', 'FAILED', 'RUN_ID_NOT_FOUND']) {
    assert.equal(verificationIsSaving({ workflow_status, persistence_state: 'pending' }), true)
    assert.equal(verificationIsSaving({ workflow_status, persistence_state: 'stored' }), false)
    assert.equal(verificationIsSaving({ workflow_status, persistence_state: 'blocked' }), false)
    assert.equal(verificationIsSaving({ workflow_status }), false)
  }
  assert.equal(verificationIsSaving(null), false)
})
