import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import { parse, compileScript } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import * as status from '../app/utils/verification-status.ts'

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
      throw new Error(`Unexpected dependency ${name}`)
    },
    useRuntimeConfig: () => ({ public: { api_url: 'https://api.test' } }),
    useToast: () => ({ add() {} }), $fetch: fetch,
    AbortController, Date, Set,
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
