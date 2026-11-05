import assert from 'node:assert/strict'
import test from 'node:test'
import { recoveryMode } from '../app/utils/verification-status.ts'
import {
  MAX_REVERIFY_SIMULATORS,
  groupDerivedLinks,
  reverifyBody,
  reverifyDraftFromOffer,
  reverifyErrors
} from '../app/utils/verification-recovery.ts'

const report = (workflow_status, persistence_state = 'stored') => ({ workflow_id: 'a', workflow_status, persistence_state })

test('recovery appears unprompted only for an unavailable original, on request for finished reports', () => {
  assert.equal(recoveryMode(null, true), 'automatic')
  assert.equal(recoveryMode(report('COMPLETED'), false), 'on_request')
  assert.equal(recoveryMode(report('FAILED'), false), 'on_request')
  // Running, saving, blocked and not-found reports offer nothing
  assert.equal(recoveryMode(report('IN_PROGRESS', 'pending'), false), 'none')
  assert.equal(recoveryMode(report('PENDING', 'pending'), false), 'none')
  assert.equal(recoveryMode(report('COMPLETED', 'pending'), false), 'none')
  assert.equal(recoveryMode(report('COMPLETED', 'blocked'), false), 'none')
  assert.equal(recoveryMode(report('RUN_ID_NOT_FOUND'), false), 'none')
  assert.equal(recoveryMode(null, false), 'none')
})

test('the re-verify draft starts from the documented defaults and is valid', () => {
  const draft = reverifyDraftFromOffer({
    reconstruct: { eligible: false },
    reverify_archive: {
      eligible: true, default_simulators: ['copasi', 'tellurium'], default_cache_buster: '0',
      defaults: { user_description: 'reverification', include_outputs: false, rel_tol: 0.0001, abs_tol_min: 0.001, abs_tol_scale: 0.00001 }
    }
  })
  assert.deepEqual(draft.simulators, ['copasi', 'tellurium'])
  assert.equal(draft.rel_tol, '0.0001')
  assert.deepEqual(reverifyErrors(draft), [])
  assert.deepEqual(reverifyBody(draft), {
    simulators: ['copasi', 'tellurium'], include_outputs: false, user_description: 'reverification',
    rel_tol: 0.0001, abs_tol_min: 0.001, abs_tol_scale: 0.00001, cache_buster: '0'
  })
  // Without an offer the same defaults the server documents are used
  assert.deepEqual(reverifyDraftFromOffer(null).simulators, ['amici', 'copasi', 'pysces', 'tellurium', 'vcell'])
})

test('client-side bounds mirror ReverifyRequest', () => {
  const valid = reverifyDraftFromOffer(null)
  const errors = changes => reverifyErrors({ ...valid, ...changes })
  assert.match(errors({ simulators: [] }).join(), /at least one simulator/)
  assert.match(errors({ simulators: ['  '] }).join(), /at least one simulator/)
  const tooMany = Array.from({ length: MAX_REVERIFY_SIMULATORS + 1 }, (_, index) => `sim${index}`)
  assert.match(errors({ simulators: tooMany }).join(), /at most 10/)
  assert.match(errors({ simulators: ['copasi', 'copasi'] }).join(), /once/)
  for (const field of ['rel_tol', 'abs_tol_min', 'abs_tol_scale']) {
    for (const bad of ['-0.1', '', 'abc', 'Infinity']) {
      assert.equal(errors({ [field]: bad }).length, 1, `${field}=${bad}`)
    }
    assert.deepEqual(errors({ [field]: '0' }), [])
  }
})

test('derived links group by relation and count the ones left out', () => {
  const link = (workflow_id, initiator_relation, is_yours = false) => ({
    workflow_id, derivation_kind: 'reverification', created: '2026-11-01T00:00:00Z', initiator_relation, is_yours
  })
  const { groups, more } = groupDerivedLinks([
    link('a', 'admin'), link('o', 'owner', true), link('y', 'other', true),
    link('x1', 'anonymous'), link('x2', 'other'), link('legacy', null)
  ], 108)
  assert.deepEqual(groups.map(group => [group.key, group.items.map(item => item.workflow_id)]), [
    ['owner_admin', ['a', 'o']], ['yours', ['y']], ['others', ['x1', 'x2', 'legacy']]
  ])
  assert.equal(more, 102)
  assert.deepEqual(groupDerivedLinks([], 0), { groups: [], more: 0 })
})
