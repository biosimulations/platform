export interface LabeledIdentifier {
  label: string
  uri?: string | null
}

export interface PageProjectFile {
  format: string
  location: string
  size: number
  url: string
}

export interface AlgorithmDetails {
  id: string
  name?: string | null
  url?: string | null
  description?: string | null
}

export interface ExecutionReason {
  message?: string | null
  type?: string | null
}

export interface PageSedLineStyle {
  color?: string | null
  thickness?: number | null
  type?: string | null
}

export interface PageSedMarkerStyle {
  fillColor?: string | null
  lineColor?: string | null
  lineThickness?: number | null
  size?: number | null
  type?: string | null
}

export interface PageSedCurveStyle {
  base?: string | null
  line?: PageSedLineStyle | null
  marker?: PageSedMarkerStyle | null
}

export interface PageGenerator {
  id: string
  name?: string | null
}

export interface PageSedCurve {
  id: string
  name?: string | null
  xDataGenerator: string | PageGenerator
  yDataGenerator: string | PageGenerator
  style?: string | PageSedCurveStyle | null
}

export interface PageSedDataSet {
  id: string
  label: string
  name?: string | null
}

export interface PageSedOutput {
  _type: 'SedPlot2D' | 'SedReport' | string
  id: string
  name?: string | null
  xScale?: 'linear' | 'log' | string | null
  yScale?: 'linear' | 'log' | string | null
  curves?: PageSedCurve[] | null
  dataSets?: PageSedDataSet[] | null
}

export interface PageSimulationRunSedDocument {
  id: string
  outputs: PageSedOutput[]
}

export interface PageLogEntity {
  id?: string
  location?: string
  status: string
  algorithm?: AlgorithmDetails | null
  skipReason?: ExecutionReason | null
  exception?: ExecutionReason | null
  output?: string | null
}

export interface PageDataSetLog {
  id?: string | null
  status?: string | null
  output?: string | null
}

export interface PageOutputLog extends PageLogEntity {
  dataSets?: PageDataSetLog[] | null
}

export interface PageSedDocumentLog extends PageLogEntity {
  location: string
  tasks?: PageLogEntity[] | null
  outputs?: PageOutputLog[] | null
}

export interface PageSimulationLog extends PageLogEntity {
  sedDocuments?: PageSedDocumentLog[] | null
}

export interface PageSimulationRun {
  id: string
  name: string
  simulator: string
  simulatorVersion: string
  submitted: string
  updated: string
  status: 'CREATED' | 'QUEUED' | 'RUNNING' | 'PROCESSING' | 'SUCCEEDED' | 'FAILED' | string
}

export interface PageSimulationRunSummary {
  name?: string | null
  description?: string | null
  abstract?: string | null
  creators: LabeledIdentifier[]
  keywords: LabeledIdentifier[]
  thumbnails: string[]
  projectSize?: number | null
  resultsSize?: number | null
}

export interface RunsPagePayload {
  info: PageSimulationRun
  summary: PageSimulationRunSummary
  files: PageProjectFile[]
  specifications: PageSimulationRunSedDocument[]
  logs: PageSimulationLog | null
}

export interface PageProjectOverview {
  id: string
  created: string
  updated: string
}

export interface PageSimulator {
  name: string
  version: string
}

export interface PageProjectSimulationRun extends PageSimulationRunSummary {
  id: string
  name: string
  citations: LabeledIdentifier[]
  encodes: LabeledIdentifier[]
  simulator: PageSimulator
  modelFormats: string[]
}

export interface ProjectsPagePayload {
  project: PageProjectOverview
  simulationRun: PageProjectSimulationRun
  files: PageProjectFile[]
  specifications: PageSimulationRunSedDocument[]
}

export type PageFile = PageProjectFile
export type ProjectFile = PageProjectFile
export type PageSpecification = PageSimulationRunSedDocument
export type SimulationRunSedDocument = PageSimulationRunSedDocument
export type PageProjectRun = PageProjectSimulationRun
export type PageRunInfo = PageSimulationRun
export type PageRunSummary = PageSimulationRunSummary
export type SimulationRunSummary = PageSimulationRunSummary
export type ExecutionSkipReason = ExecutionReason
export type ExecutionException = ExecutionReason
