export type VerifyWorkflowStatus = 'PENDING' | 'IN_PROGRESS' | 'COMPLETED' | 'FAILED' | 'RUN_ID_NOT_FOUND'

export interface CompareSettings {
  user_description: string
  include_outputs: boolean
  rel_tol: number
  abs_tol_min: number
  abs_tol_scale: number
  observables?: string[] | null
}

export interface BiosimSimulationRunMeta {
  id: string
  name?: string | null
  simulator?: string | null
  simulatorVersion?: string | null
  simulator_version?: {
    id: string
    name: string
    version: string
    image_url?: string | null
    image_digest?: string | null
  } | null
  status?: string | null
  error_message?: string | null
}

export interface HDF5DatasetMeta {
  name: string
  shape: number[]
  attributes?: Array<{ key: string, value: any }>
}

export interface HDF5GroupMeta {
  name: string
  datasets: HDF5DatasetMeta[]
  attributes?: Array<{ key: string, value: any }>
}

export interface HDF5FileMeta {
  filename?: string
  id?: string
  uri?: string
  groups: HDF5GroupMeta[]
}

export interface SimulationRunInfo {
  biosim_sim_run: BiosimSimulationRunMeta
  hdf5_file: HDF5FileMeta
}

export interface ComparisonStatistics {
  dataset_name: string
  simulator_version_i: string
  simulator_version_j: string
  var_names: string[]
  score?: number[] | null
  is_close?: boolean[] | null
  error_message?: string | null
}

export interface Hdf5DataValues {
  shape: number[]
  values: number[]
}

export interface RunData {
  run_id: string
  dataset_name: string
  var_names: string[]
  data: Hdf5DataValues
}

export interface GenerateStatisticsActivityOutput {
  sims_run_info: SimulationRunInfo[]
  comparison_statistics: Record<string, ComparisonStatistics[][]>
  sim_run_data?: RunData[] | null
}

export interface VerifyWorkflowOutput {
  workflow_id: string
  compare_settings: CompareSettings
  workflow_status: VerifyWorkflowStatus
  timestamp: string
  workflow_run_id?: string | null
  workflow_error?: string | null
  workflow_results?: GenerateStatisticsActivityOutput | null
  owner_sub?: string | null
}

// GET /verify/runs/precheck, the server-side eligibility decision (backend biosim_verify/precheck.py).
// Codes are contract values; the page shows the RunPrecheckResult the composable maps this into.
export type PrecheckExclusion = 'not_found' | 'not_succeeded' | 'upstream_unavailable'
export type PrecheckIneligibleReason = 'fewer_than_two_eligible_runs' | 'no_common_datasets'

export interface PrecheckDataset {
  name: string
  shape: number[]
  sedml_data_set_ids?: string[]
  sedml_data_set_labels?: string[]
}

export interface PrecheckRun {
  run_id: string
  eligible: boolean
  // "<simulator>:<version>" when the run record names both
  simulator?: string | null
  run_status?: string | null
  exclusion?: PrecheckExclusion | null
  reason?: string | null
  // null when the run's metadata could not be loaded
  datasets?: PrecheckDataset[] | null
}

export interface PrecheckWarning {
  code: 'metadata_unavailable' | 'shape_mismatch'
  run_ids: string[]
  dataset?: string | null
}

export interface RunPrecheckResponse {
  // Request order
  runs: PrecheckRun[]
  eligible: boolean
  ineligible_reason?: PrecheckIneligibleReason | null
  // Dataset name → the SED-ML labels every eligible run shares for it
  common_datasets: Record<string, string[]>
  warnings?: PrecheckWarning[]
}

// The pre-check result the verify-model page shows and keeps in sessionStorage
export interface ExcludedRunDetail {
  run_id: string
  simulator?: string
  status?: string
  reason: string
}

export interface RunPrecheckResult {
  valid_runs: string[]
  excluded_runs: ExcludedRunDetail[]
  common_datasets: string[]
  dataset_shapes: Record<string, number[]>
  dataset_var_names: Record<string, string[]>
  can_proceed: boolean
  warning_message?: string
}

// Augmented comparison row model
export interface VariableComparisonRow {
  var_name: string
  is_close: boolean | null
  score: number | null
  maximum_error: number | null
  relative_error: number | null
  outlier_simulators: string[]
  consensus_status: 'concordant' | 'discordant' | 'outlier' | 'unknown'
}

// OMEX Compatibility Check Models
export interface KisaoTerm {
  id: string
  name: string
}

export interface SimulationRequirement {
  algorithm: KisaoTerm
  simulation_type: string
}

export interface ModelFormat {
  format_uri: string
  language?: string | null
  location: string
}

export interface OmexContent {
  model_formats: ModelFormat[]
  simulations: SimulationRequirement[]
  sedml_files: string[]
  parse_errors?: string[]
}

export interface SimulatorVersionDetail {
  version: string
  image_url?: string | null
  algorithms?: KisaoTerm[]
  exact: boolean
  common_ancestor?: KisaoTerm | null
  equivalence_category?: KisaoTerm | null
}

export interface EligibleSimulator {
  id: string
  name: string
  versions: string[]
  exact: boolean
  version_details?: SimulatorVersionDetail[] | null
}

export interface CompatibilityResponse {
  omex_id: string
  omex_content: OmexContent
  eligible_simulators: EligibleSimulator[]
}

export type VerificationType = 'omex' | 'runs'

// One listing row of GET /verification_ids (contract proposed on #136)
export interface VerificationSummary {
  workflow_id: string
  verify_type: VerificationType
  created: string
  omex_hash?: string | null
  // Terminal status once the server has observed it; null means not known to be finished yet
  status?: VerifyWorkflowStatus | null
}

export interface VerificationIdsResponse {
  verification_ids: string[]
  // Same rows as verification_ids, in the same order; absent on backends that predate #136
  verifications?: VerificationSummary[]
  next_cursor?: string | null
}

// A listing row as the page holds it: older backends only return the workflow ID
export type VerificationListItem = Pick<VerificationSummary, 'workflow_id'> & Partial<VerificationSummary>
