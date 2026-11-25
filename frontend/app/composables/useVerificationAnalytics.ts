import type {
  ComparisonStatistics,
  ExcludedRunDetail,
  PrecheckExclusion,
  PrecheckIneligibleReason,
  PrecheckWarning,
  RunData,
  RunPrecheckResponse,
  RunPrecheckResult,
  VariableComparisonRow
} from '~/models/verification'

// GET /verify/runs/precheck limits: the backend's MAX_VERIFY_RUN_IDS and its ASCII run-ID rule
const MAX_PRECHECK_RUN_IDS = 10
const RUN_ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/

const MALFORMED_RUN_ID_REASON = 'Not a valid BioSimulations run ID.'
const UNREACHABLE_MESSAGE = 'Unable to reach the server to fetch run metadata. Please check your connection and try again.'

// The copy signed off with the precheck contract. The backend sends each exclusion's reason;
// these cover a response without one.
const EXCLUSION_FALLBACK_REASONS: Record<PrecheckExclusion, string> = {
  not_found: 'Run not found in BioSimulations.',
  not_succeeded: 'Only successful runs can be compared.',
  upstream_unavailable: 'Could not check this run. Please retry.'
}

const INELIGIBLE_MESSAGES: Record<PrecheckIneligibleReason, string> = {
  fewer_than_two_eligible_runs: 'At least two successful runs are required for comparison.',
  no_common_datasets: 'The selected runs have no datasets in common.'
}

function blockedPrecheck(excludedRuns: ExcludedRunDetail[], message: string): RunPrecheckResult {
  return {
    valid_runs: [],
    excluded_runs: excludedRuns,
    common_datasets: [],
    dataset_shapes: {},
    dataset_var_names: {},
    can_proceed: false,
    warning_message: message
  }
}

function precheckFailureMessage(err: any): string {
  const status: number | undefined = err?.statusCode ?? err?.response?.status
  // No HTTP status: the request never reached the Platform API (network, DNS or CORS)
  if (!status) return UNREACHABLE_MESSAGE
  if (status === 429) {
    const retryAfter = err?.response?.headers?.get?.('retry-after')
    return retryAfter
      ? `Too many pre-checks from this network. Please wait ${retryAfter} seconds and try again.`
      : 'Too many pre-checks from this network. Please wait a minute and try again.'
  }
  const detail = err?.data?.detail
  return typeof detail === 'string' && detail
    ? `The run pre-check failed: ${detail}`
    : `The run pre-check failed (HTTP ${status}). Please try again.`
}

// Every condition gets its own sentence: one message must not hide another
function precheckWarningMessage(
  ineligibleReason: PrecheckIneligibleReason | null,
  excludedRuns: ExcludedRunDetail[],
  warnings: PrecheckWarning[]
): string | undefined {
  const parts: string[] = []
  if (ineligibleReason) {
    parts.push(INELIGIBLE_MESSAGES[ineligibleReason] ?? 'The selected runs cannot be compared.')
  }
  for (const run of excludedRuns) {
    parts.push(`Excluded ${run.run_id}: ${run.reason}`)
  }
  const shapeMismatches = warnings.filter(w => w.code === 'shape_mismatch')
  if (shapeMismatches.length > 0) {
    const affected = shapeMismatches.map(w => `${w.dataset ?? 'a dataset'} in ${w.run_ids.join(', ')}`).join('; ')
    parts.push(`Some shared datasets have different shapes (${affected}). Comparison may be incomplete.`)
  }
  const withoutMetadata = warnings.filter(w => w.code === 'metadata_unavailable').flatMap(w => w.run_ids)
  if (withoutMetadata.length > 0) {
    parts.push(`Dataset compatibility could not be fully checked; comparison may fail (no dataset metadata for ${withoutMetadata.join(', ')}).`)
  }
  return parts.length > 0 ? parts.join(' ') : undefined
}

export function useVerificationAnalytics() {
  /**
   * Extracts a single 1D trajectory array for a specific variable name from RunData.
   */
  function extractTrajectory(runData: RunData, varName: string): number[] | null {
    if (!runData || !runData.data || !runData.var_names) return null

    const varIndex = runData.var_names.indexOf(varName)
    if (varIndex === -1) return null

    const shape = runData.data.shape
    const values = runData.data.values
    if (!shape || !values) return null

    // 2D shape: [num_vars, num_timepoints]
    if (shape.length === 2) {
      const numTimepoints = shape[1]!
      const start = varIndex * numTimepoints
      const end = start + numTimepoints
      if (values.length >= end) {
        return values.slice(start, end)
      }
    } else if (shape.length === 1 && varIndex === 0) {
      return values.slice()
    }

    return null
  }

  /**
   * Attempts to extract the time axis from RunData or generates index array.
   */
  function extractTimePoints(runData: RunData): number[] | null {
    if (!runData || !runData.var_names || !runData.data?.shape) return null

    // Find time variable by common naming conventions
    const timeIdx = runData.var_names.findIndex((v) => {
      const lower = v.toLowerCase()
      return lower === 'time' || lower.endsWith('_time') || lower.includes('autogen_time')
    })

    if (timeIdx !== -1) {
      const traj = extractTrajectory(runData, runData.var_names[timeIdx]!)
      if (traj && traj.length > 0) return traj
    }

    const numTimepoints = runData.data.shape[1] || runData.data.shape[0] || 0
    if (numTimepoints > 0) {
      return Array.from({ length: numTimepoints }, (_, i) => i)
    }

    return null
  }

  /**
   * Computes maximum error max_t |y1(t) - y2(t)| and max relative error.
   */
  function calculateTrajectoryErrors(
    y1: number[],
    y2: number[]
  ): { maximum_error: number | null, relative_error: number | null } {
    if (!y1 || !y2 || y1.length === 0 || y2.length === 0) {
      return { maximum_error: null, relative_error: null }
    }

    const len = Math.min(y1.length, y2.length)
    let maxError = 0
    let maxRelError = 0
    let validPoints = 0

    for (let t = 0; t < len; t++) {
      const v1 = y1[t]!
      const v2 = y2[t]!
      if (isNaN(v1) || isNaN(v2) || !isFinite(v1) || !isFinite(v2)) continue

      const diff = Math.abs(v1 - v2)
      if (diff > maxError) {
        maxError = diff
      }

      const denominator = Math.max(Math.abs(v1), Math.abs(v2), 1e-9)
      const relDiff = diff / denominator
      if (relDiff > maxRelError) {
        maxRelError = relDiff
      }

      validPoints++
    }

    if (validPoints === 0) {
      return { maximum_error: null, relative_error: null }
    }

    return {
      maximum_error: maxError,
      relative_error: maxRelError
    }
  }

  /**
   * Computes summary concordance for a pair comparison.
   */
  function computePairConcordance(stats?: ComparisonStatistics | null): {
    concordantCount: number
    totalCount: number
    percentage: number
    isError: boolean
    errorMessage?: string
  } {
    if (!stats) {
      return { concordantCount: 0, totalCount: 0, percentage: 0, isError: true }
    }

    if (stats.error_message) {
      return {
        concordantCount: 0,
        totalCount: stats.var_names?.length || 0,
        percentage: 0,
        isError: true,
        errorMessage: stats.error_message
      }
    }

    const varCount = stats.var_names?.length || 0
    if (varCount === 0) {
      // Nothing was compared; totalCount 0 tells callers not to present this as agreement
      return { concordantCount: 0, totalCount: 0, percentage: 0, isError: false }
    }

    let concordantCount = 0
    if (stats.is_close && stats.is_close.length === varCount) {
      concordantCount = stats.is_close.filter(Boolean).length
    } else if (stats.score && stats.score.length === varCount) {
      concordantCount = stats.score.filter(s => s >= 0.999).length
    } else {
      return {
        concordantCount: 0,
        totalCount: varCount,
        percentage: 0,
        isError: true,
        errorMessage: 'Comparison evaluation scores missing from solver results.'
      }
    }

    // Floor, so 100% only ever means every variable agreed (199/200 must not round up)
    const percentage = Math.floor((concordantCount / varCount) * 100)
    return { concordantCount, totalCount: varCount, percentage, isError: false }
  }

  /**
   * Summarizes variable agreement over every simulator pair of one dataset matrix.
   * Pairs whose comparison errored are reported, not silently dropped.
   */
  function summarizeConcordance(matrix: (ComparisonStatistics | undefined)[][]): {
    percentage: number | null
    concordantCount: number
    totalCount: number
    comparedPairs: number
    totalPairs: number
  } {
    let concordantCount = 0
    let totalCount = 0
    let comparedPairs = 0
    let totalPairs = 0

    for (let i = 0; i < matrix.length; i++) {
      for (let j = i + 1; j < matrix.length; j++) {
        totalPairs++
        const stats = matrix[i]?.[j] || matrix[j]?.[i]
        if (!stats || stats.error_message || !stats.is_close || stats.is_close.length === 0) continue
        comparedPairs++
        totalCount += stats.is_close.length
        concordantCount += stats.is_close.filter(Boolean).length
      }
    }

    return {
      // null when nothing was compared: fewer than two solvers, or every pair errored
      percentage: totalCount > 0 ? Math.floor((concordantCount / totalCount) * 100) : null,
      concordantCount,
      totalCount,
      comparedPairs,
      totalPairs
    }
  }

  /**
   * Detects consensus outliers across N simulators for a specific variable.
   */
  function detectConsensusOutliers(
    simulators: string[],
    varName: string,
    pairStatsMatrix: (ComparisonStatistics | undefined)[][]
  ): { outliers: string[], status: 'concordant' | 'discordant' | 'outlier' | 'unknown' } {
    const n = simulators.length
    if (n < 2) return { outliers: [], status: 'unknown' }

    if (n === 2) {
      const stats = pairStatsMatrix[0]?.[1] || pairStatsMatrix[1]?.[0]
      if (!stats || stats.error_message) {
        return { outliers: [], status: 'discordant' }
      }
      const varIdx = stats.var_names.indexOf(varName)
      const isClose = varIdx !== -1 && stats.is_close ? Boolean(stats.is_close[varIdx]) : (stats.score ? stats.score[varIdx]! >= 0.99 : false)
      return {
        outliers: [],
        status: isClose ? 'concordant' : 'discordant'
      }
    }

    // For N >= 3: construct agreement matrix
    const agreement: boolean[][] = Array.from({ length: n }, () => Array(n).fill(false))
    for (let i = 0; i < n; i++) {
      agreement[i]![i] = true
      for (let j = i + 1; j < n; j++) {
        const stats = pairStatsMatrix[i]?.[j] || pairStatsMatrix[j]?.[i]
        let isClose = false
        if (stats && !stats.error_message) {
          const varIdx = stats.var_names.indexOf(varName)
          if (varIdx !== -1) {
            if (stats.is_close && stats.is_close[varIdx] !== undefined) {
              isClose = Boolean(stats.is_close[varIdx])
            } else if (stats.score && stats.score[varIdx] !== undefined) {
              isClose = stats.score[varIdx]! >= 0.99
            }
          }
        }
        agreement[i]![j] = isClose
        agreement[j]![i] = isClose
      }
    }

    // Count agreements for each simulator
    const agreementCounts = simulators.map((_, i) => {
      let count = 0
      for (let j = 0; j < n; j++) {
        if (i !== j && agreement[i]![j]) {
          count++
        }
      }
      return count
    })

    const maxAgreements = n - 1
    const allAgree = agreementCounts.every(count => count === maxAgreements)
    if (allAgree) {
      return { outliers: [], status: 'concordant' }
    }

    // Majority threshold: >= ceil(maxAgreements / 2)
    const majorityThreshold = Math.ceil(maxAgreements / 2)
    const consensusSimulators: string[] = []
    const outliers: string[] = []

    simulators.forEach((sim, i) => {
      if (agreementCounts[i]! >= majorityThreshold) {
        consensusSimulators.push(sim)
      } else {
        outliers.push(sim)
      }
    })

    // If a clear majority exists (at least 2 simulators) and there are outliers
    if (consensusSimulators.length >= 2 && outliers.length > 0 && outliers.length < consensusSimulators.length) {
      return { outliers, status: 'outlier' }
    }

    // If split or fragmented
    return { outliers: [], status: 'discordant' }
  }

  /**
   * Builds augmented variable comparison rows for a selected pair of simulators.
   */
  function buildVariableComparisonRows(
    pairStats: ComparisonStatistics,
    runIId?: string,
    runJId?: string,
    simRunData?: RunData[] | null,
    allSimulators?: string[],
    fullMatrix?: (ComparisonStatistics | undefined)[][]
  ): VariableComparisonRow[] {
    if (!pairStats || !pairStats.var_names) return []

    const runIData = runIId && simRunData ? simRunData.find(r => r.run_id === runIId && r.dataset_name === pairStats.dataset_name) : null
    const runJData = runJId && simRunData ? simRunData.find(r => r.run_id === runJId && r.dataset_name === pairStats.dataset_name) : null

    return pairStats.var_names.map((varName, idx) => {
      const isClose = pairStats.is_close && pairStats.is_close.length > idx ? Boolean(pairStats.is_close[idx]) : null
      const score = pairStats.score && pairStats.score.length > idx ? (pairStats.score[idx] ?? null) : null

      let maximumError: number | null = null
      let relativeError: number | null = null

      if (runIData && runJData) {
        const y1 = extractTrajectory(runIData, varName)
        const y2 = extractTrajectory(runJData, varName)
        if (y1 && y2) {
          const errors = calculateTrajectoryErrors(y1, y2)
          maximumError = errors.maximum_error
          relativeError = errors.relative_error
        }
      }

      let outlierSimulators: string[] = []
      let consensusStatus: 'concordant' | 'discordant' | 'outlier' | 'unknown' = 'unknown'

      if (allSimulators && fullMatrix && allSimulators.length >= 2) {
        const consensus = detectConsensusOutliers(allSimulators, varName, fullMatrix)
        outlierSimulators = consensus.outliers
        consensusStatus = consensus.status
      } else {
        consensusStatus = isClose === true ? 'concordant' : isClose === false ? 'discordant' : 'unknown'
      }

      return {
        var_name: varName,
        is_close: isClose,
        score,
        maximum_error: maximumError,
        relative_error: relativeError,
        outlier_simulators: outlierSimulators,
        consensus_status: consensusStatus
      }
    })
  }

  /**
   * Pre-checks existing run IDs with one call to the Platform API's GET /verify/runs/precheck,
   * which applies the same loader and dataset-overlap rule as POST /verify/runs. The response is
   * mapped into the RunPrecheckResult the page shows and keeps in sessionStorage.
   */
  async function precheckRunIds(
    runIds: string[]
  ): Promise<RunPrecheckResult> {
    // Resolved before any await, while the caller's Nuxt context is current
    const apiUrl = useRuntimeConfig().public.api_url
    // The backend rejects a repeated ID with 400; a repeat adds nothing to a pairwise comparison
    const cleanedIds = [...new Set(runIds.map(id => id.trim()).filter(id => id.length > 0))]

    if (cleanedIds.length === 0) {
      return blockedPrecheck([], 'Please provide at least two simulation run IDs.')
    }

    // One malformed ID would fail the whole request with 422, so it is excluded here instead
    const wellFormedIds = cleanedIds.filter(id => RUN_ID_PATTERN.test(id))
    if (wellFormedIds.length > MAX_PRECHECK_RUN_IDS) {
      return blockedPrecheck([], `At most ${MAX_PRECHECK_RUN_IDS} runs can be compared at once; ${wellFormedIds.length} were given.`)
    }

    let response: RunPrecheckResponse | null = null
    if (wellFormedIds.length > 0) {
      const query = new URLSearchParams()
      for (const id of wellFormedIds) query.append('biosimulations_run_ids', id)
      try {
        response = await $fetch<RunPrecheckResponse>(`${apiUrl}/verify/runs/precheck?${query.toString()}`, {
          credentials: 'omit'
        })
      } catch (err: any) {
        const message = precheckFailureMessage(err)
        return blockedPrecheck(
          cleanedIds.map(id => ({ run_id: id, reason: RUN_ID_PATTERN.test(id) ? message : MALFORMED_RUN_ID_REASON })),
          message
        )
      }
    }

    const runsById = new Map((response?.runs ?? []).map(run => [run.run_id, run]))
    const validRuns: string[] = []
    const excludedRuns: ExcludedRunDetail[] = []
    for (const id of cleanedIds) {
      const run = runsById.get(id)
      if (run?.eligible) {
        validRuns.push(id)
      } else if (!RUN_ID_PATTERN.test(id)) {
        excludedRuns.push({ run_id: id, reason: MALFORMED_RUN_ID_REASON })
      } else {
        // JSON.stringify drops undefined fields, so saved results keep their earlier shape
        excludedRuns.push({
          run_id: id,
          simulator: run?.simulator ?? undefined,
          status: run?.run_status ?? undefined,
          reason: run?.reason || EXCLUSION_FALLBACK_REASONS[run?.exclusion ?? 'upstream_unavailable']
        })
      }
    }

    // Shapes and variable names come from the first eligible run that has metadata
    const commonDatasets = Object.keys(response?.common_datasets ?? {})
    const reference = (response?.runs ?? []).find(run => run.eligible && Array.isArray(run.datasets))
    const datasetShapes: Record<string, number[]> = {}
    const datasetVarNames: Record<string, string[]> = {}
    for (const name of commonDatasets) {
      const dataset = reference?.datasets?.find(d => d.name === name)
      if (dataset) {
        datasetShapes[name] = dataset.shape
        datasetVarNames[name] = dataset.sedml_data_set_ids ?? []
      }
    }

    const canProceed = Boolean(response?.eligible)
    const ineligibleReason = canProceed
      ? null
      : (response?.ineligible_reason ?? 'fewer_than_two_eligible_runs')
    return {
      valid_runs: validRuns,
      excluded_runs: excludedRuns,
      common_datasets: commonDatasets,
      dataset_shapes: datasetShapes,
      dataset_var_names: datasetVarNames,
      can_proceed: canProceed,
      warning_message: precheckWarningMessage(ineligibleReason, excludedRuns, response?.warnings ?? [])
    }
  }

  return {
    extractTrajectory,
    extractTimePoints,
    calculateTrajectoryErrors,
    computePairConcordance,
    summarizeConcordance,
    detectConsensusOutliers,
    buildVariableComparisonRows,
    precheckRunIds
  }
}
