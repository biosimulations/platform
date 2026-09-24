export enum SimulatorCurationStatus {
  'Registered with BioSimulators' = 1,
  'Algorithms curated' = 2,
  'Parameters curated' = 3,
  'Image available' = 4,
  'Image validated' = 5,
}

export interface RawSimulatorAlgorithm {
  id: string
  name?: string | null
  kisaoId?: { id: string; namespace: string } | null
  modelingFrameworks?: Array<{ id: string; namespace: string }> | null
  modelFormats?: Array<{ id: string; namespace: string; version?: string | null }> | null
  simulationFormats?: Array<{ id: string; namespace: string; version?: string | null }> | null
  archiveFormats?: Array<{ id: string; namespace: string; version?: string | null }> | null
  parameters?: any[] | null
  citations?: any[] | null
}

export interface RawSimulatorAuthor {
  firstName?: string | null
  middleName?: string | null
  lastName?: string | null
  identifiers?: any[] | null
}

export interface RawSimulator {
  id: string
  name: string
  version: string
  description?: string | null
  urls?: Array<{ type?: string | null; title?: string | null; url: string }> | null
  authors?: RawSimulatorAuthor[] | null
  references?: { citations?: any[]; identifiers?: any[] } | null
  algorithms?: RawSimulatorAlgorithm[] | null
  interfaceTypes?: string[] | null
  supportedOperatingSystemTypes?: string[] | null
  supportedProgrammingLanguages?: Array<{ id: string } | string> | null
  funding?: Array<{ funder?: { id: string } }> | null
  biosimulators?: {
    validated?: boolean
    imageVersion?: string
    specificationVersion?: string
    updated?: string
    created?: string
  } | null
  image?: {
    url?: string
    digest?: string
    operatingSystemType?: string
    format?: any
  } | null
  cli?: {
    package?: string
    command?: string
    installationInstructions?: string
    packageRepository?: string
  } | null
  pythonApi?: {
    package?: string
    module?: string
    installationInstructions?: string
  } | null
  license?: {
    id: string
    namespace: string
  } | null
}

export interface TableSimulator {
  id: string
  name: string
  version: string
  description: string
  url: string | null
  curationStatus: SimulatorCurationStatus
  curationStatusMessage: string
  frameworks: string[]
  frameworkIds: string[]
  algorithms: string[]
  algorithmIds: string[]
  modelFormats: string[]
  modelFormatIds: string[]
  simulationFormats: string[]
  simulationFormatIds: string[]
  interfaceTypes: string[]
  supportedOperatingSystemTypes: string[]
  supportedProgrammingLanguages: string[]
  image?: string
  cli?: string
  pythonApi?: string
  license: string | null
  licenseId: string | null
  updated: string | null
  authors: string[]
}

export const KNOWN_EDAM_FORMATS: Record<string, string> = {
  format_2332: 'XML',
  format_2585: 'SBML',
  format_3240: 'CellML',
  format_3621: 'SQLite format',
  format_3685: 'SED-ML',
  format_3971: 'NeuroML',
  format_3972: 'BNGL',
  format_9000: 'Virtual Cell Markup Language',
  format_9001: 'Smoldyn configuration language',
  format_9002: 'Morpheus Markup Language',
  format_9004: 'Low Entropy Model Specification',
  format_9005: 'High Order Calculator',
  format_9006: 'Kappa',
  format_9008: 'GINsim Markup Language',
  format_9010: 'XPP',
  format_9012: 'Resource Balance Analysis XML'
}

export const KNOWN_SBO_FRAMEWORKS: Record<string, string> = {
  SBO_0000292: 'Spatial continuous',
  SBO_0000293: 'Non-spatial continuous',
  SBO_0000294: 'Spatial discrete',
  SBO_0000295: 'Non-spatial discrete',
  SBO_0000547: 'Boolean logical',
  SBO_0000624: 'Flux balance',
  SBO_0000675: 'Deterministic continuous',
  SBO_0000676: 'Stochastic continuous',
  SBO_0000678: 'Particle discrete spatial',
  SBO_0000680: 'Particle discrete non-spatial',
  SBO_0000684: 'Hybrid flux balance-deterministic',
  SBO_0000686: 'Hybrid flux balance-logical',
  SBO_0000690: 'Hybrid deterministic-discrete',
  SBO_0000691: 'Hybrid stochastic-discrete',
  SBO_0000692: 'Resource balance'
}

export function getSimulatorCurationStatus(simulator: RawSimulator): SimulatorCurationStatus {
  let curationStatus = SimulatorCurationStatus['Registered with BioSimulators']
  if (simulator.algorithms && simulator.algorithms.length > 0) {
    curationStatus = SimulatorCurationStatus['Algorithms curated']

    let parametersCurated = true
    for (const algorithm of simulator.algorithms) {
      if (algorithm.parameters == null) {
        parametersCurated = false
        break
      }
    }

    if (parametersCurated) {
      curationStatus = SimulatorCurationStatus['Parameters curated']

      if (simulator.image) {
        curationStatus = SimulatorCurationStatus['Image available']

        if (simulator.biosimulators?.validated) {
          curationStatus = SimulatorCurationStatus['Image validated']
        }
      }
    }
  }

  return curationStatus
}

export function getSimulatorCurationStatusMessage(status: SimulatorCurationStatus, showLabel = true): string {
  const statusNames: Record<SimulatorCurationStatus, string> = {
    [SimulatorCurationStatus['Registered with BioSimulators']]: 'Registered with BioSimulators',
    [SimulatorCurationStatus['Algorithms curated']]: 'Algorithms curated',
    [SimulatorCurationStatus['Parameters curated']]: 'Parameters curated',
    [SimulatorCurationStatus['Image available']]: 'Image available',
    [SimulatorCurationStatus['Image validated']]: 'Image validated',
  }
  const label = showLabel ? ` ${statusNames[status] || ''}` : ''
  return '★'.repeat(status) + '☆'.repeat(5 - status) + label
}
