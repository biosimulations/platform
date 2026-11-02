import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import { parse, compileScript } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import * as status from '../app/utils/verification-status.ts'
import * as recoveryUtils from '../app/utils/verification-recovery.ts'

// Mount the real SFC setup/lifecycle/watchers with Vue's renderer and a minimal host.
// No copied polling implementation, browser, or live backend is involved.
function mountStatus(fetch, initial = { workflowId: 'a' }) {
  const source = readFileSync(new URL('../app/components/verification/VerificationStatus.vue', import.meta.url), 'utf8')
  const { descriptor } = parse(source)
  const compiled = compileScript(descriptor, { id: 'verification-status-test' }).content
  const module = { exports: {} }
  const timers = new Map()
  let timerId = 0
  vm.runInNewContext(ts.transpileModule(compiled, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
  }).outputText, {
    exports: module.exports, module,
    require(name) {
      if (name === 'vue') return Vue
      if (name === '@vueuse/core') return { useClipboard: () => ({ copy() {} }) }
      if (name === '~/utils/verification-status') return status
      if (name === '~/utils/verification-recovery') return recoveryUtils
      throw new Error(`Unexpected dependency ${name}`)
    },
    useRuntimeConfig: () => ({ public: { api_url: 'https://api.test' } }),
    useToast: () => ({ add() {} }), $fetch: fetch,
    AbortController, Date, JSON, Map, Math, Set, Uint8Array, crypto,
    setInterval: (fn) => {
      timers.set(++timerId, fn)
      return timerId
    },
    clearInterval: id => timers.delete(id)
  })
  const component = module.exports.default
  component.render = () => null
  const host = Vue.createRenderer({
    createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
    setText() {}, setElementText() {}, patchProp() {}, insert() {}, remove() {},
    parentNode: () => null, nextSibling: () => null
  })
  const props = Vue.reactive(initial)
  const events = []
  let instance
  const root = { setup: () => () => Vue.h(component, {
    ...props, ref: (value) => { instance = value?.$ },
    'onUpdate:modelValue': output => events.push(['update', output]),
    onCompleted: output => events.push(['completed', output]),
    onFailed: error => events.push(['failed', error])
  }) }
  const app = host.createApp(root)
  app.mount({})
  return { props, events, timers, state: () => instance.setupState,
    tick: async () => {
      for (const fn of [...timers.values()]) fn()
      await flush()
    },
    unmount: () => app.unmount() }
}
const flush = async () => {
  await new Promise(resolve => setImmediate(resolve))
  await Vue.nextTick()
}
const output = (id, persistence_state = 'stored') => ({ workflow_id: id, workflow_status: 'COMPLETED', persistence_state })
const deferred = () => {
  let resolve, reject
  const promise = new Promise((r, j) => {
    resolve = r
    reject = j
  })
  return { promise, resolve, reject }
}

test('actual component keeps polling while saving and emits completion after acknowledgement', async () => {
  let calls = 0
  const view = mountStatus(async () => output('a', ++calls === 1 ? 'pending' : 'stored'))
  await flush()
  assert.equal(view.events.filter(e => e[0] === 'completed').length, 0)
  for (let i = 0; i < 5; i++) await view.tick()
  assert.equal(calls, 2)
  assert.equal(view.events.filter(e => e[0] === 'completed').length, 1)
  assert.equal(view.timers.size, 0)
  view.unmount()
})

test('actual component aborts switched requests and discards late success and failure', async () => {
  for (const rejectOld of [false, true]) {
    const first = deferred()
    const signals = []
    const view = mountStatus((url, options) => {
      signals.push(options.signal)
      return url.endsWith('/a') ? first.promise : Promise.resolve(output('b'))
    })
    view.props.workflowId = 'b'
    await flush()
    assert.equal(signals[0].aborted, true)
    if (rejectOld) first.reject(new Error('late private failure'))
    else first.resolve(output('a'))
    await flush()
    assert.equal(view.state().workflowData.workflow_id, 'b')
    assert.equal(view.state().errorMsg, null)
    assert.equal(view.events.some(e => e[1]?.workflow_id === 'a'), false)
    view.unmount()
  }
})

test('actual component halts expired and forbidden lookups; expiry has no futile retry', async () => {
  for (const code of [404, 403]) {
    let calls = 0
    const view = mountStatus(async () => {
      calls++
      throw { response: { status: code }, data: { detail: code === 404 ? 'exists but its results are no longer retained.' : 'Forbidden' } }
    })
    await flush()
    assert.equal(view.state().status, 'UNAVAILABLE')
    assert.equal(view.state().unavailable, code === 404)
    assert.equal(view.timers.size, 0)
    await view.tick()
    assert.equal(calls, 1)
    view.unmount()
  }
})

test('actual component backs off transient errors, prevents overlap and aborts on unmount', async () => {
  const pending = deferred()
  let calls = 0, signal
  const view = mountStatus((url, options) => {
    signal = options.signal
    if (++calls === 1) return Promise.reject({ response: { status: 503 } })
    return pending.promise
  })
  await flush()
  for (let i = 0; i < 9; i++) await view.tick()
  assert.equal(calls, 1)
  await view.tick()
  assert.equal(calls, 2)
  for (let i = 0; i < 30; i++) await view.tick()
  assert.equal(calls, 2)
  view.unmount()
  assert.equal(signal.aborted, true)
  pending.resolve(output('a'))
  await flush()
  assert.equal(view.events.some(e => e[0] === 'completed'), false)
})

const recoveryOffer = {
  reconstruct: {
    eligible: true, message: 'This starts a new calculation from the retained numerical inputs.',
    run_ids: ['run0'], snapshot_sha256: ['abc'],
    compare_settings: { user_description: 'kept', include_outputs: false, rel_tol: 0.0001, abs_tol_min: 0.001, abs_tol_scale: 0.00001 },
  },
  reverify_archive: {
    eligible: true, may_start_simulations: true, cache_reuse: 'not_guaranteed',
    message: 'This starts a new verification and can submit new simulation jobs.',
    defaults: { user_description: 'reverification', include_outputs: false, rel_tol: 0.0001, abs_tol_min: 0.001, abs_tol_scale: 0.00001 },
    default_simulators: ['amici', 'copasi', 'pysces', 'tellurium', 'vcell'],
    default_cache_buster: '0', suggested_simulators: ['copasi:4.45.296'],
  },
}

test('recovery shows defaults, reuses one idempotency key, and keeps the original workflow id', async () => {
  const calls = []
  const posted = deferred()
  const view = mountStatus((url, options = {}) => {
    calls.push({ url, method: options.method, headers: options.headers })
    if (options.method === 'POST') return posted.promise
    throw {
      response: { status: 404 },
      data: { detail: 'exists but its results are no longer retained.', recovery: recoveryOffer },
    }
  })
  await flush()
  assert.equal(view.state().unavailable, true)
  assert.equal(view.state().recovery.reverify_archive.may_start_simulations, true)
  assert.equal(view.state().reverifyDraft.user_description, 'reverification')
  assert.deepEqual(view.state().reverifyDraft.simulators, ['amici', 'copasi', 'pysces', 'tellurium', 'vcell'])
  view.state().reverifyConfirmed = true
  const first = view.state().submitRecovery('reverify')
  const second = view.state().submitRecovery('reverify')
  await flush()
  const posts = calls.filter(call => call.method === 'POST')
  assert.equal(posts.length, 2)
  assert.equal(posts[0].headers['Idempotency-Key'], posts[1].headers['Idempotency-Key'])
  assert.match(posts[0].url, /\/verify\/a\/reverify$/)
  view.state().reverifyDraft.user_description = 'changed'
  view.state().submitRecovery('reverify')
  await flush()
  const postsAfter = calls.filter(call => call.method === 'POST')
  assert.notEqual(postsAfter[2].headers['Idempotency-Key'], postsAfter[0].headers['Idempotency-Key'])
  posted.resolve({ workflow_id: 'reverification-new' })
  await first
  await second
  await flush()
  assert.equal(view.props.workflowId, 'a')
  assert.equal(view.state().linkedVerificationId, 'reverification-new')
  view.unmount()
})

test('a late recovery response does not attach to a different workflow', async () => {
  const late = deferred()
  const view = mountStatus((url, options = {}) => {
    if (options.method === 'POST') return late.promise
    if (String(url).endsWith('/b')) return output('b')
    throw {
      response: { status: 404 },
      data: { detail: 'exists but its results are no longer retained.', recovery: recoveryOffer },
    }
  })
  await flush()
  const pending = view.state().submitRecovery('reconstruct')
  view.props.workflowId = 'b'
  await flush()
  late.resolve({ workflow_id: 'reconstruction-late' })
  await pending
  await flush()
  assert.equal(view.props.workflowId, 'b')
  assert.equal(view.state().linkedVerificationId, null)
  assert.equal(view.state().workflowData.workflow_id, 'b')
  view.unmount()
})

const derivedLinks = [
  { workflow_id: 'reconstruction-1', derivation_kind: 'reconstruction', created: '2026-10-29T12:00:00Z' },
  { workflow_id: 'reverification-2', derivation_kind: 'reverification', created: '2026-10-29T13:00:00Z' },
]

test('the original permalink lists derived verifications whether its report is stored or unavailable', async () => {
  const view = mountStatus(async (url) => {
    if (String(url).endsWith('/stored')) return { ...output('stored'), derived_verifications: derivedLinks }
    throw {
      response: { status: 404 },
      data: {
        detail: 'Verification gone exists but its results are no longer retained.',
        recovery: recoveryOffer, derived_verifications: [derivedLinks[1]],
      },
    }
  }, { workflowId: 'stored' })
  await flush()
  assert.deepEqual(view.state().derivedVerifications.map(item => item.workflow_id), ['reconstruction-1', 'reverification-2'])
  // The original result stays on screen; the links sit beside it
  assert.equal(view.state().workflowData.workflow_id, 'stored')

  view.props.workflowId = 'gone'
  await flush()
  assert.equal(view.state().unavailable, true)
  assert.deepEqual(view.state().derivedVerifications.map(item => item.workflow_id), ['reverification-2'])
  assert.equal(view.props.workflowId, 'gone')
  view.unmount()
})

test('switching workflows clears derived links from the previous one', async () => {
  const view = mountStatus(async url => String(url).endsWith('/a')
    ? { ...output('a'), derived_verifications: derivedLinks }
    : output('b'))
  await flush()
  assert.equal(view.state().derivedVerifications.length, 2)
  view.props.workflowId = 'b'
  await flush()
  assert.equal(view.state().derivedVerifications.length, 0)
  view.unmount()
})

test('recovery idempotency keys fit the server pattern and differ per request body', async () => {
  const keys = []
  const view = mountStatus((url, options = {}) => {
    if (options.method === 'POST') {
      keys.push(options.headers['Idempotency-Key'])
      return Promise.resolve({ workflow_id: `reconstruction-${keys.length}` })
    }
    throw {
      response: { status: 404 },
      data: { detail: 'exists but its results are no longer retained.', recovery: recoveryOffer },
    }
  })
  await flush()
  await view.state().submitRecovery('reconstruct')
  view.state().reverifyConfirmed = true
  await view.state().submitRecovery('reverify')
  assert.equal(keys.length, 2)
  for (const key of keys) assert.match(key, /^[A-Za-z0-9._:-]{8,200}$/)
  assert.notEqual(keys[0], keys[1])
  view.unmount()
})

// --- Where recovery actions appear (S-1) ------------------------------------------------

const unavailableError = (extra = {}) => ({
  response: { status: 404 },
  data: { detail: 'exists but its results are no longer retained.', recovery: recoveryOffer, ...extra },
})

test('recovery visibility: nothing while running or saving, on request when finished, unprompted when unavailable', async () => {
  const cases = [
    { name: 'running', answer: { ...output('a', 'pending'), workflow_status: 'IN_PROGRESS' }, mode: 'none' },
    { name: 'saving', answer: output('a', 'pending'), mode: 'none' },
    { name: 'stored', answer: output('a'), mode: 'on_request' },
    { name: 'failed', answer: { ...output('a'), workflow_status: 'FAILED' }, mode: 'on_request' },
    { name: 'unavailable', error: unavailableError(), mode: 'automatic' },
  ]
  for (const item of cases) {
    const urls = []
    const view = mountStatus(async (url) => {
      urls.push(String(url))
      if (item.error) throw item.error
      return item.answer
    })
    await flush()
    assert.equal(view.state().mode, item.mode, item.name)
    // Only the unavailable original shows actions without being asked
    assert.equal(view.state().showRecoveryActions, item.mode === 'automatic', item.name)
    assert.equal(urls.some(url => url.endsWith('/recovery')), false, item.name)
    view.unmount()
  }
})

test('a finished report loads its recovery offers once, when Recompute is opened', async () => {
  const urls = []
  const view = mountStatus(async (url) => {
    urls.push(String(url))
    return String(url).endsWith('/recovery') ? recoveryOffer : output('a')
  })
  await flush()
  assert.equal(view.state().showRecoveryActions, false)
  await view.state().toggleRecompute()
  await flush()
  assert.deepEqual(urls.filter(url => url.endsWith('/recovery')), ['https://api.test/verify/a/recovery'])
  assert.equal(view.state().showRecoveryActions, true)
  await view.state().toggleRecompute()
  assert.equal(view.state().showRecoveryActions, false)
  await view.state().toggleRecompute()
  assert.equal(urls.filter(url => url.endsWith('/recovery')).length, 1)
  assert.equal(view.state().showRecoveryActions, true)
  view.unmount()
})

test('edited re-verify settings reach the request body; invalid ones are not sent', async () => {
  const posts = []
  const view = mountStatus(async (url, options = {}) => {
    if (options.method === 'POST') {
      posts.push(options.body)
      return { workflow_id: 'reverification-edited' }
    }
    throw unavailableError()
  })
  await flush()
  const draft = view.state().reverifyDraft
  draft.simulators = ['copasi', 'tellurium:2.2']
  draft.rel_tol = '0.5'
  draft.abs_tol_min = '0'
  draft.abs_tol_scale = '0.25'
  draft.include_outputs = true
  draft.user_description = 'my settings'
  view.state().reverifyConfirmed = true
  await view.state().submitRecovery('reverify')
  assert.deepEqual(posts, [{
    simulators: ['copasi', 'tellurium:2.2'], include_outputs: true, user_description: 'my settings',
    rel_tol: 0.5, abs_tol_min: 0, abs_tol_scale: 0.25, cache_buster: '0',
  }])
  view.state().reverifyDraft.rel_tol = '-1'
  await view.state().submitRecovery('reverify')
  assert.equal(posts.length, 1)
  assert.match(view.state().recoveryError, /Relative tolerance/)
  view.state().addSimulator('vcell:7')
  assert.deepEqual(view.state().reverifyDraft.simulators, ['copasi', 'tellurium:2.2', 'vcell:7'])
  view.unmount()
})

test('derived verifications are grouped by relation with a count of the rest', async () => {
  const links = [
    { workflow_id: 'admin-1', derivation_kind: 'reconstruction', created: '2026-11-01T00:00:00Z', initiator_relation: 'admin' },
    { workflow_id: 'mine-1', derivation_kind: 'reverification', created: '2026-11-01T00:00:00Z', initiator_relation: 'other', is_yours: true, custom_settings: true },
    { workflow_id: 'other-1', derivation_kind: 'reverification', created: '2026-11-01T00:00:00Z', initiator_relation: 'anonymous' },
  ]
  const view = mountStatus(async url => String(url).endsWith('/a')
    ? { ...output('a'), derived_verifications: links, derived_verifications_total: 107 }
    : output('b'))
  await flush()
  const { groups, more } = view.state().derivedView
  assert.deepEqual(groups.map(group => group.key), ['owner_admin', 'yours', 'others'])
  assert.equal(groups[1].items[0].custom_settings, true)
  assert.equal(more, 104)
  view.props.workflowId = 'b'
  await flush()
  assert.equal(view.state().derivedView.groups.length, 0)
  view.unmount()
})
