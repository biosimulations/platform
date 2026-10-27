import type { SimulatorCurationStatus } from './simulator-table'

export interface SimulatorAuthor {
  name: string
  orcidUrl: string | null
}

export interface SimulatorCitation {
  text: string
  url: string | null
}

export interface SimulatorIdentifier {
  text: string
  url: string
}

export interface SimulatorFunding {
  funderName: string
  funderUrl: string
  grant: string | null
  url: string | null
}

export interface SimulatorParameter {
  id: string
  name: string
  kisaoId: string
  kisaoUrl: string
  type: string
  rawValue: string | null
  value: any
  formattedValue: string | null
  recommendedRange: string[] | null
  formattedRange: string[] | null
  formattedKisaoRange?: Array<{ id: string; name: string; url: string }> | null
  availableSoftwareInterfaceTypes: string[]
}

export interface SimulatorAlgorithmFramework {
  id: string
  name: string
  url: string
}

export interface SimulatorAlgorithmFormat {
  id: string
  name: string
  url: string
  version: string | null
  supportedFeatures: string[]
}

export interface SimulatorAlgorithmChangePattern {
  name: string
  types: Array<{ name: string; value: string }>
  target?: { grammar: string; value: string } | null
  symbol?: { namespace: string; value: string } | null
}

export interface SimulatorAlgorithm {
  id: string
  kisaoId: string
  kisaoUrl: string
  name: string
  description: string | null
  citations: SimulatorCitation[]
  modelingFrameworks: SimulatorAlgorithmFramework[]
  modelFormats: SimulatorAlgorithmFormat[]
  modelChangePatterns: SimulatorAlgorithmChangePattern[]
  simulationFormats: SimulatorAlgorithmFormat[]
  simulationTypes: Array<{ name: string; value: string }>
  archiveFormats: SimulatorAlgorithmFormat[]
  parameters: SimulatorParameter[]
  outputDimensions: Array<{ id: string; name: string; url: string }>
  outputVariablePatterns: Array<{ name: string; target?: any; symbol?: any }>
  dependencies: Array<{
    name: string
    version?: string
    required?: boolean
    url?: string
    freeNonCommercialLicense?: boolean
  }>
  availableSoftwareInterfaceTypes: string[]
}

export interface SimulatorVersionItem {
  version: string
  created: string
  image: { url?: string; digest?: string; operatingSystemType?: string } | null
  curationStatus: SimulatorCurationStatus
  curationStatusMessage: string
  validated: boolean
}

export interface SimulatorTestCaseResult {
  caseId: string
  description: string
  caseClass: string
  caseUrl: string
  caseArchive: string | null
  caseArchiveUrl: string | null
  resultType: 'Passed' | 'Failed' | 'Skipped' | 'Warned'
  duration: string
  exception?: { category: string; message: string } | null
  warnings: Array<{ category: string; message: string }>
  skipReason?: { category: string; message: string } | null
  log: string
}

export interface SimulatorValidationTests {
  testSuiteVersion: string
  testSuiteVersionUrl: string
  numTests: number
  numTestsPassed: number
  numTestsPassedWithWarnings: number
  numTestsSkipped: number
  numTestsFailed: number
  results: SimulatorTestCaseResult[]
  ghIssue: number
  ghIssueUrl: string
  ghActionRun: number
  ghActionRunUrl: string
}

export interface SimulatorDetail {
  id: string
  version: string
  name: string
  description: string | null
  curationStatus: SimulatorCurationStatus
  curationStatusMessage: string
  validated: boolean
  created: string
  updated: string
  licenseName: string | null
  licenseUrl: string | null
  urls: Array<{ type?: string | null; title?: string | null; url: string }>
  image: { url?: string; digest?: string; operatingSystemType?: string } | null
  cli: {
    package?: string
    command?: string
    packageRepository?: string
    installationInstructions?: string | null
  } | null
  pythonApi: {
    package?: string
    module?: string
    installationInstructions?: string | null
  } | null
  authors: SimulatorAuthor[]
  citations: SimulatorCitation[]
  identifiers: SimulatorIdentifier[]
  funding: SimulatorFunding[]
  otherInterfaceTypes: string[]
  supportedOperatingSystemTypes: string[]
  supportedProgrammingLanguages: string[]
  algorithms: SimulatorAlgorithm[]
  versions: SimulatorVersionItem[]
  validationTests: SimulatorValidationTests | null
  rawJson: string
}
