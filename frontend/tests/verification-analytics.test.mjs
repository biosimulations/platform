import assert from 'node:assert/strict'
import { test } from 'node:test'
import { $fetch } from 'ofetch'
import { useVerificationAnalytics } from '../app/composables/useVerificationAnalytics.ts'

// Nuxt normally supplies this global; individual precheck tests mock it.
globalThis.$fetch = $fetch

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

test('run precheck uses credential-free same-origin requests and preserves common datasets', async (t) => {
  const requests = []
  t.mock.method(globalThis, '$fetch', async (url, options) => {
    requests.push({ url, options })
    return url.endsWith('/metadata')
      ? { groups: [{ datasets: [{ name: 'report', shape: [2, 10], attributes: [{ key: 'sedmlDataSetIds', value: ['time', 'x'] }] }] }] }
      : { status: 'SUCCEEDED', simulator: 'test', simulatorVersion: '1' }
  })
  const result = await useVerificationAnalytics().precheckRunIds([' run-1 ', 'run_2'])
  assert.equal(result.can_proceed, true)
  assert.deepEqual(result.valid_runs, ['run-1', 'run_2'])
  assert.deepEqual(result.common_datasets, ['report'])
  assert.deepEqual(result.dataset_shapes.report, [2, 10])
  assert.deepEqual(result.dataset_var_names.report, ['time', 'x'])
  assert.deepEqual(requests.map(r => r.url), [
    '/biosimulations-api/runs/run-1', '/simdata-api/datasets/run-1/metadata',
    '/biosimulations-api/runs/run_2', '/simdata-api/datasets/run_2/metadata'
  ])
  for (const { options } of requests) {
    assert.deepEqual(options, { credentials: 'omit', headers: { accept: 'application/json' } })
  }
})

test('run precheck excludes missing runs with a clear reason and blocks fewer than two', async (t) => {
  const requests = []
  t.mock.method(globalThis, '$fetch', async (url) => {
    requests.push(url)
    if (url.includes('missing%2Fid')) throw Object.assign(new Error('Not Found'), { statusCode: 404 })
    return url.endsWith('/metadata') ? { groups: [] } : { status: 'SUCCEEDED' }
  })
  const result = await useVerificationAnalytics().precheckRunIds(['valid', 'missing/id'])
  assert.equal(result.can_proceed, false)
  assert.deepEqual(result.valid_runs, ['valid'])
  assert.equal(result.excluded_runs[0].reason, 'Run not found in BioSimulations database')
  assert.equal(requests.at(-1), '/biosimulations-api/runs/missing%2Fid')
})

test('run precheck retains successful runs when simdata fails and excludes unfinished runs', async (t) => {
  t.mock.method(globalThis, '$fetch', async (url) => {
    if (url.endsWith('/metadata')) throw Object.assign(new Error('Bad Gateway'), { statusCode: 502 })
    return { status: url.endsWith('/running') ? 'RUNNING' : 'SUCCEEDED' }
  })
  const result = await useVerificationAnalytics().precheckRunIds(['first', 'second', 'running'])
  assert.equal(result.can_proceed, true)
  assert.deepEqual(result.valid_runs, ['first', 'second'])
  assert.deepEqual(result.common_datasets, [])
  assert.equal(result.excluded_runs[0].status, 'RUNNING')
})
