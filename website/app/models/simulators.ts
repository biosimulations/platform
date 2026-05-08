// <editor-fold desc="For parsing OMEX/COMBINE Archive files to submit a simulation run request"
export interface Simulator {
  id: string
  name: string
  versions: string[]
  exact: boolean

  _selected_version: string | undefined
}
export interface ModelFormat {
  format_uri: string
  language: string
  location: string
}
export interface Simulation {
  algorithm_kisao_id: string
  simulation_type: string
}
export interface OmexContent {
  model_formats: ModelFormat[]
  simulations: Simulation[]
  sedml_files: string[] // Array of file paths/names
}
export interface ArchiveCompatibilityResponse {
  omex_id: string
  omex_content: OmexContent
  eligible_simulators: Simulator[]
}
//</editor-fold>

//<editor-fold desc="For submitting a simulation run request"
export interface SimulatorSelection {
  id: string, // e.g., "copasi"
  version: string //x.xx.xxx
}
export interface RunSimulationPayloadInterface {
  omex_id?: string // sha hash or something
  name?: string // No uniqueness constraint
  simulators: SimulatorSelection[],
  email_address?: string
  is_commercial: boolean // Default to false
  newsletter_consent: boolean

  _simulators: Simulator[]
}

export class RunSimulationPayload implements RunSimulationPayloadInterface {
  constructor(
    public omex_id: string | undefined = undefined,
    public name: string | undefined = undefined,
    public simulators: SimulatorSelection[] = [],
    public email_address: string | undefined = undefined,
    public is_commercial: boolean = false,
    public newsletter_consent: boolean = false,
    public _simulators: Simulator[] = []
  ) {}
}

//</editor-fold>

//<editor-fold desc="For once a simulation run request is submitted"
export interface ConglomerateStatus {
  processing_id: string
  jobs: SimulationProcessingStatus[]
}

export interface SimulationProcessingStatus {
  job_id: string // Compute the result URL off of this, the backend treats it the same
  status: 'processing' | 'success' | 'failure'
  simulator_id: string
  version: string | undefined
  error: string | undefined
  biosimulations_run_id: string
}
//</editor-fold>

// <editor-fold desc="For when viewing a simulation result">
export interface SimulationRun {
  id: string // uuid
  name: string
  simulator: string
  simulatorVersion: string
  simulatorDigest: string
  cpus: number
  memory: number
  maxTime: number
  envVars: string[]
  purpose: string
  email: string
  status: string
  runtime: number
  projectSize: number
  resultsSize: number
  submitted: string
  updated: string
}
// </editor-fold>

// <editor-fold desc="For when viewing projects">
export interface Project {
  id: string
  simulationRun: string
  created: string
  updated: string
}
// </editor-fold>
