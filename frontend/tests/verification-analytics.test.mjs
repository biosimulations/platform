import assert from 'node:assert/strict'
import { test } from 'node:test'
import { useVerificationAnalytics } from '../app/composables/useVerificationAnalytics.ts'

// Nuxt normally supplies these globals; individual precheck tests mock $fetch.
const api = 'https://api.biosim.example'
globalThis.useRuntimeConfig = () => ({ public: { api_url: api } })
globalThis.$fetch = async () => {
  throw new Error('$fetch must be mocked in tests')
}

const {
  extractTrajectory,
  extractTimePoints,
  calculateTrajectoryErrors,
  computePairConcordance,
  summarizeConcordance,
  detectConsensusOutliers,
  buildVariableComparisonRows
} = useVerificationAnalytics()

// One ComparisonStatistics cell, shaped like the backend's generate_statistics_activity output
const pair = (fields = {}) => ({
  dataset_name: 'report',
  simulator_version_i: 'a:1',
  simulator_version_j: 'b:1',
  var_names: ['x', 'y'],
  ...fields
})

const agree = names => pair({ var_names: names, is_close: names.map(() => true), score: names.map(() => 0.1) })
const disagree = names => pair({ var_names: names, is_close: names.map(() => false), score: names.map(() => 5) })

// Full N×N matrix the way the backend sends it, including the diagonal
const matrixOf = (n, cell) => Array.from({ length: n }, (_, i) => Array.from({ length: n }, (_, j) => cell(i, j)))

test('trajectories are read row-major from the backend\'s [num_vars, num_timepoints] layout', () => {
  const run = { run_id: 'r1', dataset_name: 'report', var_names: ['time', 'x'], data: { shape: [2, 3], values: [0, 1, 2, 10, 11, 12] } }
  assert.deepEqual(extractTrajectory(run, 'x'), [10, 11, 12])
  assert.deepEqual(extractTimePoints(run), [0, 1, 2])
  assert.equal(extractTrajectory(run, 'missing'), null)

  const noTime = { ...run, var_names: ['x', 'y'] }
  assert.deepEqual(extractTimePoints(noTime), [0, 1, 2])
})

test('trajectory errors skip non-finite points and report nothing for empty input', () => {
  assert.deepEqual(calculateTrajectoryErrors([1, 2, Number.NaN, 4], [1, 2.5, 3, Number.POSITIVE_INFINITY]), {
    maximum_error: 0.5,
    relative_error: 0.2
  })
  assert.deepEqual(calculateTrajectoryErrors([], [1]), { maximum_error: null, relative_error: null })
})

test('a pair reads 100% only when every variable agreed', () => {
  const names = Array.from({ length: 200 }, (_, k) => `v${k}`)
  const oneFailure = agree(names)
  oneFailure.is_close[0] = false
  const result = computePairConcordance(oneFailure)
  assert.equal(result.concordantCount, 199)
  assert.equal(result.percentage, 99)

  assert.equal(computePairConcordance(agree(names)).percentage, 100)
})

test('a pair with nothing to compare is not presented as agreement', () => {
  const empty = computePairConcordance(pair({ var_names: [], is_close: [], score: [] }))
  assert.equal(empty.totalCount, 0)
  assert.equal(empty.isError, false)
  assert.notEqual(empty.percentage, 100)

  const errored = computePairConcordance(pair({ error_message: 'Shapes do not match' }))
  assert.equal(errored.isError, true)
  assert.equal(errored.errorMessage, 'Shapes do not match')
  assert.equal(computePairConcordance(undefined).isError, true)
})

test('overall concordance is null when fewer than two solvers produced results', () => {
  const summary = summarizeConcordance(matrixOf(1, () => agree(['x'])))
  assert.equal(summary.percentage, null)
  assert.equal(summary.totalPairs, 0)
  assert.equal(summarizeConcordance([]).percentage, null)
})

test('overall concordance is null when every pair errored, and counts the errored pairs', () => {
  const summary = summarizeConcordance(matrixOf(3, (i, j) => (i === j ? agree(['x']) : pair({ error_message: 'Variables do not match' }))))
  assert.equal(summary.percentage, null)
  assert.equal(summary.comparedPairs, 0)
  assert.equal(summary.totalPairs, 3)
})

test('overall concordance covers compared pairs only and reports partial coverage', () => {
  const matrix = matrixOf(3, (i, j) => {
    if (i === j) return agree(['x', 'y'])
    if ((i === 0 && j === 1) || (i === 1 && j === 0)) {
      return pair({ var_names: ['x', 'y', 'z', 'w'], is_close: [true, true, true, false], score: [0.1, 0.1, 0.1, 3] })
    }
    return pair({ error_message: 'Dataset report not found' })
  })
  assert.deepEqual(summarizeConcordance(matrix), {
    percentage: 75,
    concordantCount: 3,
    totalCount: 4,
    comparedPairs: 1,
    totalPairs: 3
  })
})

test('overall concordance falls back to the lower triangle and floors the percentage', () => {
  const names = Array.from({ length: 200 }, (_, k) => `v${k}`)
  const nearly = agree(names)
  nearly.is_close[7] = false
  const summary = summarizeConcordance([[agree(names), undefined], [nearly, agree(names)]])
  assert.equal(summary.comparedPairs, 1)
  assert.equal(summary.percentage, 99)
})

test('consensus: two solvers agree or disagree, three solvers single out the outlier', () => {
  assert.equal(detectConsensusOutliers(['A', 'B'], 'x', matrixOf(2, () => agree(['x']))).status, 'concordant')
  assert.equal(detectConsensusOutliers(['A', 'B'], 'x', matrixOf(2, (i, j) => (i === j ? agree(['x']) : disagree(['x'])))).status, 'discordant')

  // A and B agree; C disagrees with both
  const outlierC = matrixOf(3, (i, j) => (i === j || i + j === 1 ? agree(['x']) : disagree(['x'])))
  assert.deepEqual(detectConsensusOutliers(['A', 'B', 'C'], 'x', outlierC), { outliers: ['C'], status: 'outlier' })
  assert.deepEqual(detectConsensusOutliers(['A', 'B', 'C'], 'x', matrixOf(3, () => agree(['x']))), { outliers: [], status: 'concordant' })
  assert.equal(detectConsensusOutliers(['A'], 'x', matrixOf(1, () => agree(['x']))).status, 'unknown')
})

test('variable rows combine pair statistics, trajectory errors and consensus', () => {
  const stats = pair({ var_names: ['x'], is_close: [false], score: [4] })
  const runData = [
    { run_id: 'r1', dataset_name: 'report', var_names: ['x'], data: { shape: [1, 3], values: [1, 2, 3] } },
    { run_id: 'r2', dataset_name: 'report', var_names: ['x'], data: { shape: [1, 3], values: [1, 2, 4] } }
  ]
  const matrix = matrixOf(2, (i, j) => (i === j ? agree(['x']) : stats))
  const [row] = buildVariableComparisonRows(stats, 'r1', 'r2', runData, ['A', 'B'], matrix)
  assert.equal(row.var_name, 'x')
  assert.equal(row.is_close, false)
  assert.equal(row.score, 4)
  assert.equal(row.maximum_error, 1)
  assert.equal(row.relative_error, 0.25)
  assert.equal(row.consensus_status, 'discordant')
})


// GET /verify/runs/precheck response pieces, shaped like backend biosim_verify/precheck.py
const dataset = (name, shape, ids = ['time', 'x']) => ({ name, shape, sedml_data_set_ids: ids, sedml_data_set_labels: ids })
const eligibleRun = (runId, datasets = [dataset('report', [2, 10])], simulator = 'vcell:7.7.0.13') => ({
  run_id: runId, eligible: true, simulator, run_status: 'SUCCEEDED', exclusion: null, reason: null, datasets
})
const precheckUrl = ids => `${api}/verify/runs/precheck?${ids.map(id => `biosimulations_run_ids=${id}`).join('&')}`
// The page keeps the result in sessionStorage; this is what it reads back
const saved = result => JSON.parse(JSON.stringify(result))

function mockPrecheck(t, respond) {
  const requests = []
  t.mock.method(globalThis, '$fetch', async (url, options) => {
    requests.push({ url, options })
    return respond(url)
  })
  return requests
}

test('run precheck trims and dedupes the IDs, then makes one credential-free Platform API call', async (t) => {
  const requests = mockPrecheck(t, () => ({
    runs: [eligibleRun('run-1'), eligibleRun('run_2')],
    eligible: true,
    ineligible_reason: null,
    common_datasets: { report: ['time', 'x'] },
    warnings: []
  }))
  const result = await useVerificationAnalytics().precheckRunIds([' run-1 ', 'run_2', 'run-1', '  '])
  assert.deepEqual(requests, [{ url: precheckUrl(['run-1', 'run_2']), options: { credentials: 'omit' } }])
  // The unchanged RunPrecheckResult shape, as saved results already have it
  assert.deepEqual(saved(result), {
    valid_runs: ['run-1', 'run_2'],
    excluded_runs: [],
    common_datasets: ['report'],
    dataset_shapes: { report: [2, 10] },
    dataset_var_names: { report: ['time', 'x'] },
    can_proceed: true
  })
})

test('run precheck reads shapes and variable names from the first eligible run with metadata', async (t) => {
  mockPrecheck(t, () => ({
    runs: [
      { run_id: 'gone', eligible: false, exclusion: 'not_found', reason: 'Run not found in BioSimulations.', datasets: null },
      eligibleRun('no-metadata', null),
      eligibleRun('first', [dataset('report', [3, 5], ['time', 'a', 'b']), dataset('other', [1, 5], ['time'])]),
      eligibleRun('second', [dataset('report', [3, 5], ['t', 'a', 'b'])])
    ],
    eligible: true,
    common_datasets: { report: ['time', 'a', 'b'] },
    warnings: [{ code: 'metadata_unavailable', run_ids: ['no-metadata'], dataset: null }]
  }))
  const result = await useVerificationAnalytics().precheckRunIds(['gone', 'no-metadata', 'first', 'second'])
  assert.equal(result.can_proceed, true)
  assert.deepEqual(result.valid_runs, ['no-metadata', 'first', 'second'])
  assert.deepEqual(result.common_datasets, ['report'])
  assert.deepEqual(result.dataset_shapes, { report: [3, 5] })
  assert.deepEqual(result.dataset_var_names, { report: ['time', 'a', 'b'] })
})

test('run precheck maps exclusions in request order and keeps the saved shape', async (t) => {
  mockPrecheck(t, () => ({
    runs: [
      eligibleRun('good'),
      { run_id: 'missing', eligible: false, simulator: null, run_status: null, exclusion: 'not_found', reason: 'Run not found in BioSimulations.', datasets: null },
      { run_id: 'running', eligible: false, simulator: 'copasi:4.45', run_status: 'RUNNING', exclusion: 'not_succeeded', reason: 'Run status is RUNNING; only successful runs can be compared.', datasets: null },
      { run_id: 'flaky', eligible: false, exclusion: 'upstream_unavailable', datasets: null }
    ],
    eligible: false,
    ineligible_reason: 'fewer_than_two_eligible_runs',
    common_datasets: {},
    warnings: []
  }))
  const result = await useVerificationAnalytics().precheckRunIds(['good', 'missing', 'running', 'flaky'])
  assert.equal(result.can_proceed, false)
  assert.deepEqual(result.valid_runs, ['good'])
  assert.deepEqual(saved(result).excluded_runs, [
    { run_id: 'missing', reason: 'Run not found in BioSimulations.' },
    { run_id: 'running', simulator: 'copasi:4.45', status: 'RUNNING', reason: 'Run status is RUNNING; only successful runs can be compared.' },
    // A response without reason copy still says why
    { run_id: 'flaky', reason: 'Could not check this run. Please retry.' }
  ])
  assert.equal(result.warning_message, [
    'At least two successful runs are required for comparison.',
    'Excluded missing: Run not found in BioSimulations.',
    'Excluded running: Run status is RUNNING; only successful runs can be compared.',
    'Excluded flaky: Could not check this run. Please retry.'
  ].join(' '))
})

test('run precheck blocks a selection the backend found to share no datasets', async (t) => {
  mockPrecheck(t, () => ({
    runs: [eligibleRun('a', [dataset('report', [2, 10], ['x'])]), eligibleRun('b', [dataset('report', [2, 10], ['y'])])],
    eligible: false,
    ineligible_reason: 'no_common_datasets',
    common_datasets: {},
    warnings: []
  }))
  const result = await useVerificationAnalytics().precheckRunIds(['a', 'b'])
  assert.equal(result.can_proceed, false)
  // Both runs succeeded; the batch is what cannot be compared
  assert.deepEqual(result.valid_runs, ['a', 'b'])
  assert.deepEqual(result.excluded_runs, [])
  assert.deepEqual(result.common_datasets, [])
  assert.equal(result.warning_message, 'The selected runs have no datasets in common.')
})

test('run precheck lets shape and metadata warnings through without one hiding another', async (t) => {
  mockPrecheck(t, () => ({
    runs: [
      eligibleRun('a', [dataset('report', [2, 10])]),
      eligibleRun('b', [dataset('report', [2, 11])]),
      eligibleRun('c', null),
      { run_id: 'd', eligible: false, run_status: 'FAILED', exclusion: 'not_succeeded', reason: 'Run status is FAILED; only successful runs can be compared.' }
    ],
    eligible: true,
    common_datasets: { report: ['time', 'x'] },
    warnings: [
      { code: 'shape_mismatch', run_ids: ['a', 'b'], dataset: 'report' },
      { code: 'metadata_unavailable', run_ids: ['c'], dataset: null }
    ]
  }))
  const result = await useVerificationAnalytics().precheckRunIds(['a', 'b', 'c', 'd'])
  assert.equal(result.can_proceed, true)
  assert.deepEqual(result.valid_runs, ['a', 'b', 'c'])
  assert.equal(result.warning_message, [
    'Excluded d: Run status is FAILED; only successful runs can be compared.',
    'Some shared datasets have different shapes (report in a, b). Comparison may be incomplete.',
    'Dataset compatibility could not be fully checked; comparison may fail (no dataset metadata for c).'
  ].join(' '))
})

test('run precheck excludes malformed IDs itself instead of failing the whole request', async (t) => {
  const requests = mockPrecheck(t, () => ({
    runs: [eligibleRun('ok-1'), eligibleRun('ok_2')],
    eligible: true,
    common_datasets: { report: ['time', 'x'] },
    warnings: []
  }))
  // The backend's per-item rule is ASCII: a slash, a dot or a non-ASCII letter would be a 422
  const result = await useVerificationAnalytics().precheckRunIds(['ok-1', 'missing/id', 'ok_2', 'héllo', 'a.b'])
  assert.deepEqual(requests.map(r => r.url), [precheckUrl(['ok-1', 'ok_2'])])
  assert.equal(result.can_proceed, true)
  assert.deepEqual(result.valid_runs, ['ok-1', 'ok_2'])
  assert.deepEqual(result.excluded_runs.map(r => [r.run_id, r.reason]), [
    ['missing/id', 'Not a valid BioSimulations run ID.'],
    ['héllo', 'Not a valid BioSimulations run ID.'],
    ['a.b', 'Not a valid BioSimulations run ID.']
  ])

  const onlyMalformed = await useVerificationAnalytics().precheckRunIds(['x/y', 'z z'])
  assert.equal(requests.length, 1)
  assert.equal(onlyMalformed.can_proceed, false)
  assert.equal(onlyMalformed.excluded_runs.length, 2)
  assert.match(onlyMalformed.warning_message, /^At least two successful runs are required for comparison\./)
})

test('run precheck refuses empty and oversized selections without calling the API', async (t) => {
  const requests = mockPrecheck(t, () => {
    throw new Error('unexpected request')
  })
  const empty = await useVerificationAnalytics().precheckRunIds([' ', ''])
  assert.equal(empty.can_proceed, false)
  assert.equal(empty.warning_message, 'Please provide at least two simulation run IDs.')

  const eleven = Array.from({ length: 11 }, (_, k) => `run${k}`)
  const oversized = await useVerificationAnalytics().precheckRunIds([...eleven, 'run0'])
  assert.equal(oversized.can_proceed, false)
  assert.equal(oversized.warning_message, 'At most 10 runs can be compared at once; 11 were given.')
  assert.equal(requests.length, 0)
})

test('run precheck keeps the unreachable-server message when the Platform API cannot be reached', async (t) => {
  mockPrecheck(t, () => {
    throw new TypeError('Failed to fetch')
  })
  const result = await useVerificationAnalytics().precheckRunIds(['a', 'b'])
  const message = 'Unable to reach the server to fetch run metadata. Please check your connection and try again.'
  assert.equal(result.can_proceed, false)
  assert.equal(result.warning_message, message)
  assert.deepEqual(result.valid_runs, [])
  assert.deepEqual(result.excluded_runs, [{ run_id: 'a', reason: message }, { run_id: 'b', reason: message }])
})

test('run precheck turns API errors into readable messages', async (t) => {
  const fetchError = (status, data, headers = {}) => Object.assign(new Error(`HTTP ${status}`), {
    statusCode: status, data, response: { status, headers: new Headers(headers) }
  })
  let error
  mockPrecheck(t, () => {
    throw error
  })
  error = fetchError(429, { detail: 'Rate limit exceeded. Slow down and retry after the indicated delay.' }, { 'Retry-After': '42' })
  assert.equal((await useVerificationAnalytics().precheckRunIds(['a', 'b'])).warning_message,
    'Too many pre-checks from this network. Please wait 42 seconds and try again.')

  error = fetchError(400, { detail: 'Duplicate run IDs are not allowed: a' })
  assert.equal((await useVerificationAnalytics().precheckRunIds(['a', 'b'])).warning_message,
    'The run pre-check failed: Duplicate run IDs are not allowed: a')

  // FastAPI's 422 detail is a list; it must not render as [object Object]
  error = fetchError(422, { detail: [{ loc: ['query', 'biosimulations_run_ids', 0], msg: 'String should match pattern' }] })
  const invalid = await useVerificationAnalytics().precheckRunIds(['a', 'b'])
  assert.equal(invalid.can_proceed, false)
  assert.equal(invalid.warning_message, 'The run pre-check failed (HTTP 422). Please try again.')
})
