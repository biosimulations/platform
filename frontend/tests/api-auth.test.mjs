import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import { isPlatformApiRequest } from '../app/utils/api-auth.ts'

const api = 'https://api.biosim.example/api/v1'
const page = 'https://biosim.example'

test('only the configured API origin and path receive its token', () => {
  for (const request of [api, `${api}/me`, new Request(`${api}/me`)]) {
    assert.equal(isPlatformApiRequest(request, undefined, api, page), true)
  }
  for (const request of [
    'https://compose.example/simulation/run',
    'https://tenant.auth0.com/dbconnections/change_password',
    'https://legacy.example/projects',
    '/biosimulators-api/simulators/latest',
    '/biosimulations-api/runs/67817a2e1f52f47f628af971',
    '/simdata-api/datasets/67817a2e1f52f47f628af971/metadata',
    'https://api.biosim.example.evil.test/api/v1/me',
    'https://api.biosim.example/api/v10/me',
    'https://api.biosim.example/api/v1/../../other',
    '//evil.test/api/v1/me', '/api/v1/me',
    'https://user:password@api.biosim.example/api/v1/me'
  ]) assert.equal(isPlatformApiRequest(request, undefined, api, page), false)
})

test('baseURL resolution follows ofetch and absolute destinations override it', () => {
  assert.equal(isPlatformApiRequest('/me', api, api, page), true)
  assert.equal(isPlatformApiRequest('me', api, api, page), true)
  assert.equal(isPlatformApiRequest('https://evil.test/me', api, api, page), false)
  assert.equal(isPlatformApiRequest('//evil.test/me', api, api, page), false)
  assert.equal(isPlatformApiRequest(new Request('https://evil.test/me'), api, api, page), false)
  assert.equal(isPlatformApiRequest('/api/v1/me', undefined, '/api/v1', page), true)
})

test('missing or malformed configuration fails closed', () => {
  for (const config of [undefined, '', 'https://[invalid', 'file:///api']) {
    assert.equal(isPlatformApiRequest('/me', undefined, config, page), false)
  }
})

// Runs the real plugin source with its imports stubbed and returns its onRequest hook
function loadPluginHook(auth0) {
  let hook
  const source = readFileSync(new URL('../app/plugins/auth0.client.ts', import.meta.url), 'utf8')
    .replace(/^import .*$/gm, '')
    .replace('export default ', '')
  const context = {
    createAuth0: () => auth0,
    isPlatformApiRequest,
    // Stands in for @vueuse/core's until() on the plain-object refs used here
    until: ref => ({
      toBe: value => new Promise((resolve) => {
        const check = () => (ref.value === value ? resolve() : setTimeout(check, 0))
        check()
      })
    }),
    defineNuxtPlugin: plugin => plugin({ vueApp: { use() {} } }),
    useRuntimeConfig: () => ({ public: { api_url: api, auth0Audience: 'https://api.biosimulations.org' } }),
    window: { location: { origin: page } },
    Headers,
    $fetch: { create: (options) => { hook = options.onRequest } }
  }
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, context)
  return hook
}

test('the actual plugin attaches the right token only to Platform requests', async () => {
  let tokenCalls = 0
  const auth0 = {
    isLoading: { value: false },
    isAuthenticated: { value: true },
    async getAccessTokenSilently(options) {
      assert.equal(options.authorizationParams.audience, 'https://api.biosimulations.org')
      tokenCalls++
      return 'test-token'
    }
  }
  const hook = loadPluginHook(auth0)
  const options = {}
  await hook({ request: `${api}/me`, options })
  assert.equal(options.headers.get('Authorization'), 'Bearer test-token')
  assert.equal(options.credentials, 'include')
  const external = {}
  await hook({ request: 'https://compose.example/simulation/run', options: external })
  assert.deepEqual(external, {})
  for (const request of [
    '/biosimulators-api/simulators/latest',
    '/biosimulations-api/runs/67817a2e1f52f47f628af971',
    '/simdata-api/datasets/67817a2e1f52f47f628af971/metadata'
  ]) {
    const proxy = { credentials: 'omit' }
    await hook({ request, options: proxy })
    assert.deepEqual(proxy, { credentials: 'omit' })
  }
  assert.equal(tokenCalls, 1)
  const explicit = { headers: new Headers({ Authorization: 'Bearer explicit' }) }
  await hook({ request: `${api}/me`, options: explicit })
  assert.equal(explicit.headers.get('Authorization'), 'Bearer explicit')
  auth0.isAuthenticated.value = false
  const anonymous = {}
  await hook({ request: `${api}/me`, options: anonymous })
  assert.equal(anonymous.headers, undefined)
})

test('Platform requests made while Auth0 restores the session wait for it', async () => {
  const auth0 = {
    isLoading: { value: true },
    isAuthenticated: { value: false },
    async getAccessTokenSilently() {
      return 'restored-token'
    }
  }
  const hook = loadPluginHook(auth0)
  const options = {}
  let settled = false
  const pending = hook({ request: `${api}/verify/abc`, options }).then(() => {
    settled = true
  })
  await new Promise(resolve => setTimeout(resolve, 10))
  assert.equal(settled, false)
  auth0.isAuthenticated.value = true
  auth0.isLoading.value = false
  await pending
  assert.equal(options.headers.get('Authorization'), 'Bearer restored-token')

  // Other destinations are not held up by Auth0
  auth0.isLoading.value = true
  const external = {}
  await hook({ request: 'https://legacy.example/runs/1', options: external })
  assert.deepEqual(external, {})
})
