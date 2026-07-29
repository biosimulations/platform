import type {SedAbstractTask, SedDataGenerator, SedModel, SedOutput, SedSimulation, SedStyle} from "~/models/sedml";

export interface LabeledIdentifier {
  uri?: string | null
  label?: string | null
}

export interface DescribedIdentifier {
  uri?: string | null
  label?: string | null
  attribute_uri?: string | null
  attribute_label?: string | null
}

export interface SimulationRunTaskModel {
  uri: any
  id: any
  name?: any
  source: any
  language: Record<string, any>
}

export interface SimulationRunTaskSimulation {
  type: Record<string, any>
  uri: any
  id: any
  name?: any
  algorithm: Record<string, any>
}

export interface SimulationRunTaskSummary {
  uri: string
  id: string
  name?: string
  model: SimulationRunTaskModel
  simulation: SimulationRunTaskSimulation
}

export interface SimulationRunOutputType {
  id: string
  name: string
  url: string
}

export interface SimulationRunOutputSummary {
  type: SimulationRunOutputType
  uri: string
  name?: string
}

export interface SimulatorDetails {
  id: string
  name: string
  version: string
  digest: string
  url: string
}

export interface EnvironmentVariable {
  key: string
  value: string
}

export interface RunDetails {
  simulator: SimulatorDetails
  cpus: number
  memory: number
  maxTime: number
  envVars: EnvironmentVariable[]
  status: 'CREATED' | 'QUEUED' | 'RUNNING' | 'PROCESSING' | 'SUCCEEDED' | 'FAILED'
  runtime: number
  projectSize: number
  resultsSize: number
}

export interface SimulationRunMetadataSummary {
  uri: string
  title?: string
  abstract?: string
  description?: string
  thumbnails: string[]
  sources: any[]
  keywords: LabeledIdentifier[]
  taxa: LabeledIdentifier[]
  encodes: LabeledIdentifier[]
  predecessors: LabeledIdentifier[]
  successors: LabeledIdentifier[]
  seeAlso: LabeledIdentifier[]
  references: LabeledIdentifier[]
  identifiers: LabeledIdentifier[]
  citations: LabeledIdentifier[]
  creators: LabeledIdentifier[]
  contributors: LabeledIdentifier[]
  license?: LabeledIdentifier[]
  funders: LabeledIdentifier[]
  other: DescribedIdentifier[]
  created: string
  modified?: string[]
}

export interface SimulationRunSummary {
  id: string
  name: string
  tasks?: SimulationRunTaskSummary[]
  outputs?: SimulationRunOutputSummary[]
  run: RunDetails
  metadata?: SimulationRunMetadataSummary[]
  submitted: string
  updated: string
}

export interface ProjectFile {
  id: string
  name: string
  size: number
  format: string
  master: boolean
  url: string
  location: string
  simulationRun: string
  created: string
  updated: string
}

export interface SimulationRunSedDocumentInput {
  id: string;
  level: number;
  version: number;
  styles: SedStyle[];
  models: SedModel[];
  simulations: SedSimulation[];
  dataGenerators: SedDataGenerator[];
  outputs: SedOutput[];
  tasks: SedAbstractTask[];
}

export class SimulationRunSedDocumentInputsContainer {
  sedDocuments!: SimulationRunSedDocumentInput[];
}

export interface SimulationRunSedDocument extends SimulationRunSedDocumentInput {
  simulationRun: string;
  created: string;
  updated: string;
}
