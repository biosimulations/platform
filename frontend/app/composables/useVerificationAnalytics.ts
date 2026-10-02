import type {
  ComparisonStatistics,
  ExcludedRunDetail,
  RunData,
  RunPrecheckResult,
  VariableComparisonRow
} from '~/models/verification'

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
      return { concordantCount: 0, totalCount: 0, percentage: 100, isError: false }
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

    const percentage = Math.round((concordantCount / varCount) * 100)
    return { concordantCount, totalCount: varCount, percentage, isError: false }
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
   * Client-side precheck for existing run IDs.
   * Validates each run against the public REST API and checks dataset compatibility.
   */
  async function precheckRunIds(
    runIds: string[],
    biosimulationsApiUrl: string
  ): Promise<RunPrecheckResult> {
    const cleanedIds = runIds.map(id => id.trim()).filter(id => id.length > 0)
    const validRuns: string[] = []
    const excludedRuns: ExcludedRunDetail[] = []
    const datasetShapes: Record<string, number[]> = {}
    const datasetVarNames: Record<string, string[]> = {}

    if (cleanedIds.length === 0) {
      return {
        valid_runs: [],
        excluded_runs: [],
        common_datasets: [],
        dataset_shapes: {},
        dataset_var_names: {},
        can_proceed: false,
        warning_message: 'Please provide at least two simulation run IDs.'
      }
    }

    // Step 1: Fetch metadata for all runs directly from BioSimulations API
    const runInfos: Array<{
      id: string
      status: string
      simulator: string
      datasets?: Array<{ name: string, shape: number[], varNames?: string[] }>
    }> = []

    for (const id of cleanedIds) {
      try {
        const runRes: any = await $fetch(`${biosimulationsApiUrl}/runs/${id}`, {
          credentials: 'omit',
          headers: {
            accept: 'application/json'
          }
        })
        if (!runRes) {
          excludedRuns.push({ run_id: id, reason: 'Run not found in BioSimulations database' })
          continue
        }

        const status = runRes.status?.toUpperCase() || 'UNKNOWN'
        const simulator = `${runRes.simulator || 'unknown'}:${runRes.simulatorVersion || ''}`

        if (status !== 'SUCCEEDED') {
          excludedRuns.push({
            run_id: id,
            simulator,
            status,
            reason: `Simulation status is ${status} (expected SUCCEEDED)`
          })
          continue
        }

        // Try to fetch HDF5 metadata if available
        let datasets: Array<{ name: string, shape: number[], varNames?: string[] }> = []
        try {
          // Check simdata service or metadata endpoint
          const metaRes: any = await $fetch(`https://simdata.api.biosimulations.org/datasets/${id}/metadata`, {
            credentials: 'omit',
            headers: {
              accept: 'application/json'
            }
          })
          if (metaRes && metaRes.groups) {
            for (const group of metaRes.groups) {
              if (group.datasets) {
                for (const ds of group.datasets) {
                  const varIdsAttr = ds.attributes?.find((a: any) => a.key === 'sedmlDataSetIds')
                  const varNames = varIdsAttr ? varIdsAttr.value : []
                  datasets.push({
                    name: ds.name,
                    shape: ds.shape,
                    varNames
                  })
                }
              }
            }
          }
        } catch {
          // If simdata metadata endpoint fails or is not accessible directly, proceed with runRes
          // Backend will validate HDF5 directly during execution
          datasets = []
        }

        validRuns.push(id)
        runInfos.push({ id, status, simulator, datasets })
      } catch (err: any) {
        const msg = err?.data?.detail || err?.message || 'Network error'
        const reason = msg.includes('Failed to fetch')
          ? 'Failed to query run metadata: Browser CORS restriction. (Please verify backend/proxy connection)'
          : `Failed to query run metadata: ${msg}`
        excludedRuns.push({
          run_id: id,
          simulator: 'unknown',
          reason
        })
      }
    }

    // Step 2: If we have datasets info, find intersection and check shape compatibility
    let commonDatasets: string[] = []
    const runsWithDatasets = runInfos.filter(r => r.datasets && r.datasets.length > 0)

    if (runsWithDatasets.length > 1) {
      // Find common dataset names across runs that have datasets info
      const allDsLists = runsWithDatasets.map(r => r.datasets!.map(d => d.name))
      commonDatasets = allDsLists[0]!.filter(dsName =>
        allDsLists.every(list => list.includes(dsName))
      )

      // Check shapes of common datasets
      for (const dsName of commonDatasets) {
        const firstDs = runsWithDatasets[0]!.datasets!.find(d => d.name === dsName)
        if (firstDs) {
          datasetShapes[dsName] = firstDs.shape
          if (firstDs.varNames) {
            datasetVarNames[dsName] = firstDs.varNames
          }
        }
      }
    }

    const canProceed = validRuns.length >= 2
    let warningMessage: string | undefined = undefined

    if (excludedRuns.length > 0) {
      warningMessage = `${excludedRuns.length} run(s) excluded due to errors, non-succeeded status, or incompatibility.`
    } else if (validRuns.length < 2) {
      warningMessage = 'At least 2 successful runs are required for cross-verification.'
    }

    return {
      valid_runs: validRuns,
      excluded_runs: excludedRuns,
      common_datasets: commonDatasets,
      dataset_shapes: datasetShapes,
      dataset_var_names: datasetVarNames,
      can_proceed: canProceed,
      warning_message: warningMessage
    }
  }

  return {
    extractTrajectory,
    extractTimePoints,
    calculateTrajectoryErrors,
    computePairConcordance,
    detectConsensusOutliers,
    buildVariableComparisonRows,
    precheckRunIds
  }
}
