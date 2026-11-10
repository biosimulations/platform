import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import * as Vue from 'vue'

const api = 'https://api.biosim.example'

// Transpiles a source file and runs it as CommonJS with only the named dependencies available.
// simulator-table.ts declares an enum, which Node's type stripping cannot run directly.
function load(path, globals = {}, dependencies = {}) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8')
  const module = { exports: {} }
  vm.runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
  }).outputText, {
    exports: module.exports,
    module,
    require(name) {
      if (name in dependencies) return dependencies[name]
      throw new Error(`Unexpected dependency ${name}`)
    },
    ...globals
  })
  return module.exports
}

const table = load('../app/models/simulator-table.ts')

// A fresh module per test: the ontology name cache is module state
function useSimulatorsWith(fetch) {
  const { useSimulators } = load('../app/composables/useSimulators.ts', {
    useRuntimeConfig: () => ({ public: { api_url: api } }),
    $fetch: fetch,
    console: { warn() {} }
  }, { 'vue': Vue, '~/models/simulator-table': table })
  return useSimulators()
}

// Values built inside the vm context have its prototypes; compare them as JSON
const plain = value => JSON.parse(JSON.stringify(value))

// Owned SimulatorSpec records, shaped like backend biosim_server/simulators/models.py output
const algorithm = (fields = {}) => ({
  id: 'cvode', name: 'CVODE', kisaoId: { namespace: 'KISAO', id: 'KISAO_0000019' },
  modelingFrameworks: [{ namespace: 'SBO', id: 'SBO_0000293' }],
  modelFormats: [{ namespace: 'EDAM', id: 'format_2585', version: null, supportedFeatures: null }],
  simulationFormats: [{ namespace: 'EDAM', id: 'format_3685', version: null, supportedFeatures: null }],
  archiveFormats: null,
  parameters: [],
  ...fields
})
const spec = (fields = {}) => ({
  id: 'tellurium', name: 'tellurium', version: '2.2.10', description: null,
  algorithms: [algorithm()],
  image: { url: 'ghcr.io/biosimulators/tellurium:2.2.10', digest: null, format: null, operatingSystemType: 'Linux' },
  biosimulators: { validated: true, imageVersion: '1.0.0', specificationVersion: '1.0.0', created: '2024-01-01T00:00:00Z', updated: '2024-02-01T00:00:00Z' },
  license: { namespace: 'SPDX', id: 'Apache-2.0' },
  ...fields
})

function router(routes) {
  const requests = []
  const fetch = async (url, options) => {
    requests.push({ url, options })
    for (const [match, respond] of routes) {
      if (url === match || (match instanceof RegExp && match.test(url))) return respond(url)
    }
    throw Object.assign(new Error(`Unexpected request ${url}`), { statusCode: 404 })
  }
  return { fetch, requests }
}

test('the registry list is read from the Platform API envelope without credentials', async () => {
  const { fetch, requests } = router([
    [`${api}/simulators/latest`, () => ({ simulators: [spec()] })],
    [/\/ontologies\//, () => ({ namespace: 'X', terms: [] })]
  ])
  const raw = await useSimulatorsWith(fetch).fetchRawSimulators()
  assert.deepEqual(plain(raw), [spec()])
  assert.deepEqual(plain(requests), [{ url: `${api}/simulators/latest`, options: { credentials: 'omit' } }])
})

test('null parameters stay null, so curation ratings are not raised', async () => {
  const curated = spec({ id: 'curated', algorithms: [algorithm({ parameters: [] })] })
  const uncurated = spec({ id: 'uncurated', algorithms: [algorithm(), algorithm({ id: 'other', parameters: null })] })
  const { fetch } = router([
    [`${api}/simulators/latest`, () => ({ simulators: [curated, uncurated] })],
    [/\/ontologies\//, () => ({ namespace: 'X', terms: [] })]
  ])
  const simulators = useSimulatorsWith(fetch)
  const raw = await simulators.fetchRawSimulators()
  assert.equal(raw[1].algorithms[1].parameters, null)

  const rows = (await simulators.fetchSimulators()).map(row => [row.id, row.curationStatus])
  const Status = table.SimulatorCurationStatus
  assert.deepEqual(plain(rows), [
    ['curated', Status['Image validated']],
    ['uncurated', Status['Algorithms curated']]
  ])
})

test('an empty or missing list renders as no simulators', async () => {
  for (const body of [{ simulators: [] }, null]) {
    const { fetch } = router([[`${api}/simulators/latest`, () => body]])
    assert.deepEqual(plain(await useSimulatorsWith(fetch).fetchRawSimulators()), [])
  }
})

test('ontology names come from the terms envelope; unnamed terms are skipped', async () => {
  const { fetch, requests } = router([
    [`${api}/simulators/latest`, () => ({ simulators: [spec({ algorithms: [algorithm({ modelingFrameworks: [{ namespace: 'SBO', id: 'SBO_9999999' }] })] })] })],
    [`${api}/ontologies/SBO`, () => ({ namespace: 'SBO', terms: [{ id: 'SBO_9999999', name: 'Made-up framework' }, { id: 'SBO_0000000', name: null }] })],
    [`${api}/ontologies/EDAM`, () => ({ namespace: 'EDAM', terms: [] })]
  ])
  const simulators = useSimulatorsWith(fetch)
  await simulators.fetchSimulators()
  // The name cache fills in the background
  await new Promise(resolve => setTimeout(resolve, 0))
  assert.equal(simulators.resolveFrameworkName('SBO_9999999'), 'Made-up framework')
  assert.equal(simulators.ontologyCache.value.SBO.SBO_0000000, undefined)
  const ontologyRequest = requests.find(r => r.url === `${api}/ontologies/SBO`)
  assert.deepEqual(plain(ontologyRequest.options), { credentials: 'omit' })
})

test('the detail reads one record with tests and unwraps the version list', async () => {
  const record = spec({ version: '2.2.10', biosimulators: { validated: true, created: '2024-01-01T00:00:00Z', validationTests: { testSuiteVersion: '0.1.0', ghIssue: 1, ghActionRun: 2, results: [] } } })
  const older = spec({ version: '2.2.9', biosimulators: { validated: false, created: '2023-01-01T00:00:00Z' } })
  const { fetch, requests } = router([
    [`${api}/simulators/tellurium/latest?include_tests=true`, () => record],
    [`${api}/simulators/tellurium`, () => ({ simulatorId: 'tellurium', versions: [older, record] })],
    [/\/ontologies\//, () => ({ namespace: 'X', terms: [] })]
  ])
  const detail = await useSimulatorsWith(fetch).fetchSimulatorDetail('tellurium', 'latest')
  assert.deepEqual(requests.slice(0, 2).map(r => [r.url, plain(r.options)]), [
    [`${api}/simulators/tellurium/latest?include_tests=true`, { credentials: 'omit' }],
    [`${api}/simulators/tellurium`, { credentials: 'omit' }]
  ])
  assert.equal(detail.version, '2.2.10')
  assert.deepEqual(plain(detail.versions.map(v => [v.version, v.validated])), [['2.2.10', true], ['2.2.9', false]])
  assert.equal(detail.validationTests.testSuiteVersion, '0.1.0')
  // The JSON section shows the Platform record as served
  assert.deepEqual(JSON.parse(detail.rawJson), record)
})

test('the canonical specification link uses the resolved version and encodes each segment', async () => {
  const { fetch, requests } = router([
    [`${api}/simulators/a%20b/latest?include_tests=true`, () => spec({ id: 'a b', version: '1.0/rc 1' })],
    [`${api}/simulators/a%20b`, () => ({ simulatorId: 'a b', versions: [] })],
    [/\/ontologies\//, () => ({ namespace: 'X', terms: [] })]
  ])
  const detail = await useSimulatorsWith(fetch).fetchSimulatorDetail('a b')
  assert.equal(requests[0].url, `${api}/simulators/a%20b/latest?include_tests=true`)
  assert.equal(detail.canonicalSpecificationUrl, 'https://api.biosimulators.org/simulators/a%20b/1.0%2Frc%201')
  assert.equal(table.canonicalSpecificationUrl('vcell', '7.7.0.13'), 'https://api.biosimulators.org/simulators/vcell/7.7.0.13')
})

test('a failed version list leaves the detail page without a version selector', async () => {
  const { fetch } = router([
    [`${api}/simulators/tellurium/2.2.10?include_tests=true`, () => spec()],
    [/\/ontologies\//, () => ({ namespace: 'X', terms: [] })]
  ])
  const detail = await useSimulatorsWith(fetch).fetchSimulatorDetail('tellurium', '2.2.10')
  assert.equal(detail.version, '2.2.10')
  assert.deepEqual(plain(detail.versions), [])
})
