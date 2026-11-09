import assert from 'node:assert/strict'
import { test } from 'node:test'
import { resolveLegacySimulatorsTarget } from '../server/utils/legacy-simulators-proxy.ts'

test('registry list and ontology dumps are proxied and cacheable', () => {
  assert.deepEqual(resolveLegacySimulatorsTarget('simulators/latest', false), { upstreamPath: '/simulators/latest', cacheable: true })
  for (const ns of ['EDAM', 'SBO', 'KISAO', 'SPDX']) {
    assert.deepEqual(resolveLegacySimulatorsTarget(`ontologies/${ns}`, false), { upstreamPath: `/ontologies/${ns}`, cacheable: true })
  }
  // includeTests is meaningless for the list and must not split the cache key
  assert.deepEqual(resolveLegacySimulatorsTarget('simulators/latest', true), { upstreamPath: '/simulators/latest', cacheable: true })
})

test('per-simulator detail is proxied uncached, includeTests only when requested', () => {
  assert.deepEqual(resolveLegacySimulatorsTarget('simulators/tellurium', true), { upstreamPath: '/simulators/tellurium', cacheable: false })
  assert.deepEqual(resolveLegacySimulatorsTarget('simulators/tellurium/latest', true), { upstreamPath: '/simulators/tellurium/latest?includeTests=true', cacheable: false })
  assert.deepEqual(resolveLegacySimulatorsTarget('simulators/tellurium/2.2.10', false), { upstreamPath: '/simulators/tellurium/2.2.10', cacheable: false })
  // encodeURIComponent output passes through still encoded
  assert.equal(resolveLegacySimulatorsTarget('simulators/a%2Bb/1.0', false)?.upstreamPath, '/simulators/a%2Bb/1.0')
})

test('anything the simulators pages do not request is rejected', () => {
  for (const path of [
    '', 'simulators', 'simulators/', 'health', 'openapi.json',
    'ontologies', 'ontologies/GO', 'ontologies/edam', 'ontologies/EDAM/x',
    'simulators/a/b/c', 'simulators//latest',
    'simulators/..', 'simulators/%2e%2e/health', 'simulators/%2E%2E/%2e%2e', 'simulators/.%2e/x',
    'simulators/a%2Fb', 'simulators/a%5Cb', 'simulators/%E0%A4%A'
  ]) assert.equal(resolveLegacySimulatorsTarget(path, false), null, path)
})
