import { ref } from 'vue'
import {
  type RawSimulator,
  type TableSimulator,
  getSimulatorCurationStatus,
  getSimulatorCurationStatusMessage,
  KNOWN_EDAM_FORMATS,
  KNOWN_SBO_FRAMEWORKS,
} from '~/models/simulator-table'
import type {
  SimulatorDetail,
  SimulatorAlgorithm,
  SimulatorParameter,
  SimulatorVersionItem,
  SimulatorValidationTests,
  SimulatorTestCaseResult,
  SimulatorCitation,
  SimulatorAuthor,
  SimulatorFunding,
  SimulatorIdentifier,
} from '~/models/simulator-view'

// In-memory ontology term name cache across page sessions
const ontologyCache = ref<{
  EDAM: Record<string, string>
  SBO: Record<string, string>
  KISAO: Record<string, string>
  SPDX: Record<string, string>
}>({
  EDAM: { ...KNOWN_EDAM_FORMATS },
  SBO: { ...KNOWN_SBO_FRAMEWORKS },
  KISAO: {},
  SPDX: {},
})

const pendingOntologyFetches = new Set<string>()

export function useSimulators() {
  const runtimeConfig = useRuntimeConfig()
  const legacySimulatorsApi = runtimeConfig.public.legacy_simulators_api_url || 'https://api.biosimulators.org'

  async function fetchOntologyNamespace(namespace: 'EDAM' | 'SBO' | 'KISAO' | 'SPDX') {
    if (pendingOntologyFetches.has(namespace)) return
    pendingOntologyFetches.add(namespace)
    try {
      const terms = await $fetch<Array<{ id: string; name?: string }>>(`${legacySimulatorsApi}/ontologies/${namespace}`, {
        credentials: 'omit',
      })
      if (Array.isArray(terms)) {
        for (const term of terms) {
          if (term.id && term.name) {
            ontologyCache.value[namespace][term.id] = term.name
          }
        }
      }
    } catch (err) {
      console.warn(`Failed to fetch ontology namespace ${namespace}:`, err)
    } finally {
      pendingOntologyFetches.delete(namespace)
    }
  }

  function resolveFormatName(id: string): string {
    if (ontologyCache.value.EDAM[id]) {
      return ontologyCache.value.EDAM[id]!
    }
    fetchOntologyNamespace('EDAM')
    return id
  }

  function resolveFrameworkName(id: string): string {
    if (ontologyCache.value.SBO[id]) {
      return ontologyCache.value.SBO[id]!
    }
    fetchOntologyNamespace('SBO')
    return id
  }

  function getKisaoUrl(id: string): string {
    return `https://www.ebi.ac.uk/ols/ontologies/kisao/terms?iri=http%3A%2F%2Fwww.biomodels.net%2Fkisao%2FKISAO%23${encodeURIComponent(id)}`
  }

  function getSboUrl(id: string): string {
    return `https://www.ebi.ac.uk/ols/ontologies/sbo/terms?iri=http%3A%2F%2Fbiomodels.net%2FSBO%2F${encodeURIComponent(id)}`
  }

  function getEdamUrl(id: string): string {
    return `https://www.ebi.ac.uk/ols/ontologies/edam/terms?iri=http%3A%2F%2Fedamontology.org%2F${encodeURIComponent(id)}`
  }

  function mapRawSimulator(raw: RawSimulator): TableSimulator {
    const frameworksSet = new Set<string>()
    const frameworkIdsSet = new Set<string>()
    const algorithmsSet = new Set<string>()
    const algorithmIdsSet = new Set<string>()
    const modelFormatsSet = new Set<string>()
    const modelFormatIdsSet = new Set<string>()
    const simFormatsSet = new Set<string>()
    const simFormatIdsSet = new Set<string>()

    if (raw.algorithms) {
      for (const alg of raw.algorithms) {
        if (alg.name) {
          algorithmsSet.add(alg.name)
        } else if (alg.kisaoId?.id) {
          algorithmsSet.add(alg.kisaoId.id)
        }
        if (alg.kisaoId?.id) {
          algorithmIdsSet.add(alg.kisaoId.id)
        }

        if (alg.modelingFrameworks) {
          for (const f of alg.modelingFrameworks) {
            frameworkIdsSet.add(f.id)
            frameworksSet.add(resolveFrameworkName(f.id))
          }
        }

        if (alg.modelFormats) {
          for (const mf of alg.modelFormats) {
            modelFormatIdsSet.add(mf.id)
            modelFormatsSet.add(resolveFormatName(mf.id))
          }
        }

        if (alg.simulationFormats) {
          for (const sf of alg.simulationFormats) {
            simFormatIdsSet.add(sf.id)
            simFormatsSet.add(resolveFormatName(sf.id))
          }
        }
      }
    }

    const curationStatus = getSimulatorCurationStatus(raw)
    const curationStatusMessage = getSimulatorCurationStatusMessage(curationStatus)

    const authors = (raw.authors || [])
      .map(a => [a.firstName, a.middleName, a.lastName].filter(Boolean).join(' '))
      .filter(Boolean)

    const mainUrl = raw.urls && raw.urls.length > 0 ? raw.urls[0]?.url || null : null

    return {
      id: raw.id,
      name: raw.name || raw.id,
      version: raw.version || '',
      description: raw.description || '',
      url: mainUrl,
      curationStatus,
      curationStatusMessage,
      frameworks: Array.from(frameworksSet).sort((a, b) => a.localeCompare(b)),
      frameworkIds: Array.from(frameworkIdsSet),
      algorithms: Array.from(algorithmsSet).sort((a, b) => a.localeCompare(b)),
      algorithmIds: Array.from(algorithmIdsSet),
      modelFormats: Array.from(modelFormatsSet).sort((a, b) => a.localeCompare(b)),
      modelFormatIds: Array.from(modelFormatIdsSet),
      simulationFormats: Array.from(simFormatsSet).sort((a, b) => a.localeCompare(b)),
      simulationFormatIds: Array.from(simFormatIdsSet),
      interfaceTypes: (raw.interfaceTypes || []).slice().sort((a, b) => a.localeCompare(b)),
      supportedOperatingSystemTypes: (raw.supportedOperatingSystemTypes || []).slice().sort((a, b) => a.localeCompare(b)),
      supportedProgrammingLanguages: (raw.supportedProgrammingLanguages || [])
        .map(l => (typeof l === 'string' ? l : l.id))
        .sort((a, b) => a.localeCompare(b)),
      image: raw.image?.url,
      cli: raw.cli?.package,
      pythonApi: raw.pythonApi?.package,
      license: raw.license?.id || null,
      licenseId: raw.license?.id || null,
      updated: raw.biosimulators?.updated || null,
      authors,
    }
  }

  async function fetchSimulators(): Promise<TableSimulator[]> {
    const rawList = await $fetch<RawSimulator[]>(`${legacySimulatorsApi}/simulators/latest`, {
      credentials: 'omit',
    })
    return (rawList || []).map(mapRawSimulator)
  }

  function formatCitation(citation: any): SimulatorCitation {
    let text = `${citation.authors || ''}. ${citation.title || ''}`
    if (citation.journal) text += `. <i>${citation.journal}</i>`
    if (citation.volume) text += ` ${citation.volume}`
    if (citation.issue) text += ` (${citation.issue})`
    if (citation.pages) text += `, ${citation.pages.replace(/-+/g, '–')}`
    if (citation.year) text += ` (${citation.year}).`

    const identifier = citation?.identifiers?.[0]
    return {
      text: text.trim(),
      url: identifier?.url || null,
    }
  }

  function formatValidationTests(validationTests: any): SimulatorValidationTests | null {
    if (!validationTests || !Array.isArray(validationTests.results)) return null

    let numTestsPassed = 0
    let numTestPassedWithWarnings = 0
    let numTestsSkipped = 0
    let numTestsFailed = 0

    const results: SimulatorTestCaseResult[] = []

    for (const result of validationTests.results) {
      const hasWarnings = Array.isArray(result.warnings) && result.warnings.length > 0
      let resultType: 'Passed' | 'Failed' | 'Skipped' | 'Warned' = 'Passed'
      if (result.resultType === 'passed') {
        numTestsPassed++
        if (hasWarnings) {
          numTestPassedWithWarnings++
          resultType = 'Warned'
        } else {
          resultType = 'Passed'
        }
      } else if (result.resultType === 'skipped') {
        numTestsSkipped++
        resultType = 'Skipped'
      } else {
        numTestsFailed++
        resultType = 'Failed'
      }

      const rawCaseId: string = result.case?.id || ''
      const parts = rawCaseId.split(':')
      const caseClass = parts[0] || rawCaseId
      const caseArchiveName = parts.length > 1 ? parts[1] : null

      const caseArchiveUrl = caseArchiveName
        ? `https://github.com/biosimulators/Biosimulators_test_suite/raw/${validationTests.testSuiteVersion}/examples/${caseArchiveName}.omex`
        : null
      const caseUrl = `https://github.com/biosimulators/Biosimulators_test_suite/blob/${validationTests.testSuiteVersion}/biosimulators_test_suite/test_case/${rawCaseId.split('.')[0]}.py`

      results.push({
        caseId: rawCaseId,
        description: result.case?.description || '',
        caseClass,
        caseUrl,
        caseArchive: caseArchiveName ? `${caseArchiveName}.omex` : null,
        caseArchiveUrl,
        resultType,
        duration: typeof result.duration === 'number' ? `${result.duration.toFixed(2)}s` : String(result.duration || '0s'),
        exception: result.exception || null,
        warnings: result.warnings || [],
        skipReason: result.skipReason || null,
        log: result.log || '',
      })
    }

    return {
      testSuiteVersion: validationTests.testSuiteVersion || 'latest',
      testSuiteVersionUrl: `https://github.com/biosimulators/Biosimulators_test_suite/releases/tag/${validationTests.testSuiteVersion || 'latest'}`,
      numTests: validationTests.results.length,
      numTestsPassed,
      numTestsPassedWithWarnings: numTestPassedWithWarnings,
      numTestsSkipped,
      numTestsFailed,
      results,
      ghIssue: validationTests.ghIssue,
      ghIssueUrl: `https://github.com/biosimulators/Biosimulators/issues/${validationTests.ghIssue}`,
      ghActionRun: validationTests.ghActionRun,
      ghActionRunUrl: `https://github.com/biosimulators/Biosimulators/actions/runs/${validationTests.ghActionRun}`,
    }
  }

  async function fetchSimulatorDetail(id: string, version?: string): Promise<SimulatorDetail> {
    const verParam = version && version !== 'latest' ? encodeURIComponent(version) : 'latest'
    const [rawSim, rawVersions] = await Promise.all([
      $fetch<any>(`${legacySimulatorsApi}/simulators/${encodeURIComponent(id)}/${verParam}?includeTests=true`, {
        credentials: 'omit',
      }),
      $fetch<any[]>(`${legacySimulatorsApi}/simulators/${encodeURIComponent(id)}`, {
        credentials: 'omit',
      }).catch(() => []),
    ])

    if (!rawSim) {
      throw new Error(`Simulator "${id}" not found.`)
    }

    // Authors
    const authors: SimulatorAuthor[] = (rawSim.authors || []).map((author: any) => {
      const nameParts = [author.firstName, author.middleName, author.lastName].filter(Boolean)
      const name = nameParts.join(' ')
      let orcidUrl: string | null = null
      if (Array.isArray(author.identifiers)) {
        for (const ident of author.identifiers) {
          if (ident.namespace?.toLowerCase() === 'orcid') {
            orcidUrl = ident.url || `https://orcid.org/${ident.id}`
          }
        }
      }
      return { name, orcidUrl }
    })

    // Citations
    const citations: SimulatorCitation[] = (rawSim.references?.citations || []).map(formatCitation)

    // Identifiers
    const identifiers: SimulatorIdentifier[] = (rawSim.references?.identifiers || []).map((ident: any) => ({
      text: `${ident.namespace}:${ident.id}`,
      url: ident.url || `https://identifiers.org/${ident.namespace}:${ident.id}`,
    }))

    // Funding
    const funding: SimulatorFunding[] = (rawSim.funding || []).map((f: any) => ({
      funderName: f.funder?.id || 'Funder',
      funderUrl: `https://data.crossref.org/fundingdata/funder/${f.funder?.id || ''}`,
      grant: f.grant || null,
      url: f.url || null,
    }))

    // Algorithms
    const algorithms: SimulatorAlgorithm[] = (rawSim.algorithms || []).map((alg: any) => {
      const kisaoId = alg.kisaoId?.id || alg.id || ''
      const algName = alg.name || kisaoId
      const kisaoUrl = getKisaoUrl(kisaoId)

      // Modeling frameworks
      const modelingFrameworks = (alg.modelingFrameworks || []).map((f: any) => ({
        id: f.id,
        name: resolveFrameworkName(f.id),
        url: getSboUrl(f.id),
      }))

      // Model formats
      const modelFormats = (alg.modelFormats || []).map((mf: any) => ({
        id: mf.id,
        name: resolveFormatName(mf.id),
        url: getEdamUrl(mf.id),
        version: mf.version || null,
        supportedFeatures: mf.supportedFeatures || [],
      }))

      // Simulation formats
      const simulationFormats = (alg.simulationFormats || []).map((sf: any) => ({
        id: sf.id,
        name: resolveFormatName(sf.id),
        url: getEdamUrl(sf.id),
        version: sf.version || null,
        supportedFeatures: sf.supportedFeatures || [],
      }))

      // Archive formats
      const archiveFormats = (alg.archiveFormats || []).map((af: any) => ({
        id: af.id,
        name: resolveFormatName(af.id),
        url: getEdamUrl(af.id),
        version: af.version || null,
        supportedFeatures: af.supportedFeatures || [],
      }))

      // Simulation types
      const simulationTypes = (alg.simulationTypes || []).map((st: any) => ({
        name: typeof st === 'string' ? st.replace(/^Sed/, '').replace(/Simulation$/, '') : String(st),
        value: typeof st === 'string' ? st : String(st),
      }))

      // Model change patterns
      const modelChangePatterns = (alg.modelChangePatterns || []).map((mcp: any) => ({
        name: mcp.name || '',
        types: (mcp.types || []).map((t: any) => ({
          name: typeof t === 'string' ? t.replace(/^Sed/, '') : String(t),
          value: typeof t === 'string' ? t : String(t),
        })),
        target: mcp.target || null,
        symbol: mcp.symbol || null,
      }))

      // Parameters
      const parameters: SimulatorParameter[] = (alg.parameters || []).map((p: any) => {
        const paramKisaoId = p.kisaoId?.id || p.id || ''
        return {
          id: p.id || paramKisaoId,
          name: p.name || paramKisaoId,
          kisaoId: paramKisaoId,
          kisaoUrl: getKisaoUrl(paramKisaoId),
          type: p.type || 'string',
          rawValue: p.value != null ? String(p.value) : null,
          value: p.value,
          formattedValue: p.value != null ? String(p.value) : null,
          recommendedRange: Array.isArray(p.recommendedRange) ? p.recommendedRange.map(String) : null,
          formattedRange: Array.isArray(p.recommendedRange) ? p.recommendedRange.map(String) : null,
          availableSoftwareInterfaceTypes: p.availableSoftwareInterfaceTypes || [],
        }
      })

      // Output dimensions
      const outputDimensions = (alg.outputDimensions || []).map((od: any) => ({
        id: od.id,
        name: od.id,
        url: `https://identifiers.org/sio:${od.id}`,
      }))

      // Output variable patterns
      const outputVariablePatterns = (alg.outputVariablePatterns || []).map((ovp: any) => ({
        name: ovp.name,
        target: ovp.target || null,
        symbol: ovp.symbol || null,
      }))

      // Dependencies
      const dependencies = (alg.dependencies || []).map((dep: any) => ({
        name: dep.name,
        version: dep.version || undefined,
        required: dep.required ?? true,
        url: dep.url || undefined,
        freeNonCommercialLicense: dep.freeNonCommercialLicense ?? true,
      }))

      // Alg citations
      const algCitations = (alg.citations || []).map(formatCitation)

      return {
        id: alg.id || kisaoId,
        kisaoId,
        kisaoUrl,
        name: algName,
        description: alg.description || null,
        citations: algCitations,
        modelingFrameworks,
        modelFormats,
        modelChangePatterns,
        simulationFormats,
        simulationTypes,
        archiveFormats,
        parameters,
        outputDimensions,
        outputVariablePatterns,
        dependencies,
        availableSoftwareInterfaceTypes: alg.availableSoftwareInterfaceTypes || [],
      }
    })

    // Versions list
    const versions: SimulatorVersionItem[] = (rawVersions || []).map((v: any) => {
      const vStatus = getSimulatorCurationStatus(v)
      return {
        version: v.version,
        created: v.biosimulators?.created || v.created || '',
        image: v.image || null,
        curationStatus: vStatus,
        curationStatusMessage: getSimulatorCurationStatusMessage(vStatus),
        validated: Boolean(v.biosimulators?.validated),
      }
    }).sort((a, b) => {
      // Sort descending by date or version
      if (a.created && b.created) {
        return new Date(b.created).getTime() - new Date(a.created).getTime()
      }
      return b.version.localeCompare(a.version, undefined, { numeric: true })
    })

    // Validation test results
    const validationTests = formatValidationTests(rawSim.biosimulators?.validationTests)

    // License
    const licenseId = rawSim.license?.id || null
    const licenseName = licenseId ? (ontologyCache.value.SPDX[licenseId] || licenseId) : null
    const licenseUrl = licenseId ? `https://spdx.org/licenses/${licenseId}.html` : null

    const curationStatus = getSimulatorCurationStatus(rawSim)
    const curationStatusMessage = getSimulatorCurationStatusMessage(curationStatus)

    return {
      id: rawSim.id,
      version: rawSim.version,
      name: rawSim.name || rawSim.id,
      description: rawSim.description || null,
      curationStatus,
      curationStatusMessage,
      validated: Boolean(rawSim.biosimulators?.validated),
      created: rawSim.biosimulators?.created || '',
      updated: rawSim.biosimulators?.updated || '',
      licenseName,
      licenseUrl,
      urls: rawSim.urls || [],
      image: rawSim.image || null,
      cli: rawSim.cli || null,
      pythonApi: rawSim.pythonApi || null,
      authors,
      citations,
      identifiers,
      funding,
      otherInterfaceTypes: (rawSim.interfaceTypes || []).slice().sort(),
      supportedOperatingSystemTypes: (rawSim.supportedOperatingSystemTypes || []).slice().sort(),
      supportedProgrammingLanguages: (rawSim.supportedProgrammingLanguages || [])
        .map((l: any) => (typeof l === 'string' ? l : l.id))
        .sort(),
      algorithms,
      versions,
      validationTests,
      rawJson: JSON.stringify(rawSim, null, 2),
    }
  }

  return {
    fetchSimulators,
    fetchSimulatorDetail,
    mapRawSimulator,
    resolveFormatName,
    resolveFrameworkName,
    ontologyCache,
  }
}
