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

test('the actual plugin attaches the right token only to Platform requests', async () => {
  let hook
  let tokenCalls = 0
  const auth0 = {
    isAuthenticated: { value: true },
    async getAccessTokenSilently(options) {
      assert.equal(options.authorizationParams.audience, 'https://api.biosimulations.org')
      tokenCalls++
      return 'test-token'
    }
  }
  const source = readFileSync(new URL('../app/plugins/auth0.client.ts', import.meta.url), 'utf8')
    .replace(/^import .*$/gm, '')
    .replace('export default ', '')
  const context = {
    createAuth0: () => auth0,
    isPlatformApiRequest,
    defineNuxtPlugin: plugin => plugin({ vueApp: { use() {} } }),
    useRuntimeConfig: () => ({ public: { api_url: api, auth0Audience: 'https://api.biosimulations.org' } }),
    window: { location: { origin: page } },
    Headers,
    $fetch: { create: (options) => { hook = options.onRequest } }
  }
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, context)
  const options = {}
  await hook({ request: `${api}/me`, options })
  assert.equal(options.headers.get('Authorization'), 'Bearer test-token')
  assert.equal(options.credentials, 'include')
  const external = {}
  await hook({ request: 'https://compose.example/simulation/run', options: external })
  assert.deepEqual(external, {})
  assert.equal(tokenCalls, 1)
  const explicit = { headers: new Headers({ Authorization: 'Bearer explicit' }) }
  await hook({ request: `${api}/me`, options: explicit })
  assert.equal(explicit.headers.get('Authorization'), 'Bearer explicit')
  auth0.isAuthenticated.value = false
  const anonymous = {}
  await hook({ request: `${api}/me`, options: anonymous })
  assert.equal(anonymous.headers, undefined)
})
