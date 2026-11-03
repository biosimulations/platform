<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useClipboard } from '@vueuse/core'
import type { BreadcrumbItem } from '@nuxt/ui'
import type {
  ComparisonStatistics,
  CompatibilityResponse,
  RunPrecheckResult,
  SimulationRunInfo,
  VerificationIdsResponse,
  VerificationListItem,
  VerificationType,
  VerifyWorkflowOutput,
  VerifyWorkflowStatus
} from '~/models/verification'
import { useVerificationAnalytics } from '~/composables/useVerificationAnalytics'
import { isOmexHash, matchesVerificationFilter, OMEX_HASH_PATTERN, parseServerTimestamp, parseVerificationListing, verificationTypeOf } from '~/utils/verification-listing'
import type { ExcludedSimulatorInfo, SimulatorInfo } from '~/components/verification/VerificationHeatmap.vue'
import PlatformRunPickerModal from '~/components/verification/PlatformRunPickerModal.vue'

const config = useRuntimeConfig()
const route = useRoute()
const router = useRouter()
const toast = useToast()
const { copy } = useClipboard()
const { buildVariableComparisonRows, precheckRunIds, summarizeConcordance } = useVerificationAnalytics()

useSeoMeta({
  title: 'Model Verification Hub — BioSimulations',
  description: 'Cross-verify biomodel simulation results across multiple solvers (AMICI, COPASI, PySCeS, Tellurium, VCell).'
})

const breadcrumbs: BreadcrumbItem[] = [
  { label: 'Home', icon: 'i-lucide-home', to: '/' },
  { label: 'Utilities' },
  { label: 'Verify a Model' }
]

// ----------------------------------------------------
// 2-Tab Navigation
// ----------------------------------------------------
type Mode = 'verify' | 'lookup'
const activeMode = ref<Mode>('verify')

const tabItems = [
  {
    label: 'Verify Biomodel Archive',
    value: 'verify' as const,
    icon: 'i-lucide-file-check'
  },
  {
    label: 'Lookup Past Verifications',
    value: 'lookup' as const,
    icon: 'i-lucide-search'
  }
]

// ----------------------------------------------------
// Shared Tolerances
// ----------------------------------------------------
const toleranceSettings = reactive({
  rel_tol: 0.0001,
  abs_tol_min: 0.001,
  abs_tol_scale: 0.00001,
  // Stored by default so users can view interactive trajectory plots in VerificationPlotViewer
  include_outputs: true,
  user_description: 'model-verification'
})
const showAdvancedSettings = ref(false)

// Active Workflow / Dashboard State
const activeWorkflowId = ref<string | null>(null)
const workflowOutput = ref<VerifyWorkflowOutput | null>(null)
const isSubmitting = ref(false)
const submissionError = ref<string | null>(null)

// ----------------------------------------------------
// Ubiquitous OMEX Input Modalities
// ----------------------------------------------------
type InputSourceMode = 'file' | 'url' | 'run'
const inputSourceMode = ref<InputSourceMode>('file')

const inputSourceModes = [
  { label: 'Upload Archive', value: 'file' as const, icon: 'i-lucide-upload-cloud' },
  { label: 'Provide URL', value: 'url' as const, icon: 'i-lucide-link' },
  { label: 'Platform Run', value: 'run' as const, icon: 'i-lucide-layers' }
]

const omexFile = ref<File | null>(null)
const omexUrl = ref<string>('')
const selectedPlatformRun = ref<{
  id: string
  name: string
  simulator: string
  simulatorVersion: string
  downloadUrl: string
} | null>(null)
const isRunPickerModalOpen = ref(false)

// Compatibility Check State
const checkingCompatibility = ref(false)
const compatibilityError = ref<string | null>(null)
const compatibilityResponse = ref<CompatibilityResponse | null>(null)
// Only the latest check may write state; clearing or switching inputs makes earlier ones stale
let compatibilityRequest = 0

// Compatible Simulators & Version Selection State
export interface SimulatorOption {
  id: string
  name: string
  exact: boolean
  versions: string[]
  selectedVersion: string
  selected: boolean
}

const compatibleSimulators = ref<SimulatorOption[]>([])

// ----------------------------------------------------
// Hierarchical Historical Runs Tree State
// ----------------------------------------------------
export interface HistoricalRunRow {
  id: string
  biosimulationsRunId: string
  name: string
  simulator: string
  simulatorVersion: string
  status: string
  submitted: string
  selected: boolean
}

export interface HistoricalVersionGroup {
  version: string
  runs: HistoricalRunRow[]
  collapsed: boolean
}

export interface HistoricalSimulatorGroup {
  simulatorId: string
  simulatorName: string
  versions: Record<string, HistoricalVersionGroup>
  collapsed: boolean
}

const historicalGroups = ref<Record<string, HistoricalSimulatorGroup>>({})
const isSearchingHistoricalRuns = ref(false)
const historicalRunsError = ref<string | null>(null)
type Step2Mode = 'solvers' | 'historical'
const step2Mode = ref<Step2Mode>('solvers')

const verificationStrategyItems = computed(() => [
  {
    label: `Execute New Cross-Verification (${compatibleSimulators.value.length} Compatible Solvers Available)`,
    value: 'solvers' as const,
    icon: 'i-lucide-cpu'
  },
  {
    label: `Compare Historical Platform Runs (${isSearchingHistoricalRuns.value ? 'Searching...' : totalHistoricalRunsCount.value + ' Runs Detected'})`,
    value: 'historical' as const,
    icon: 'i-lucide-history'
  }
])

// ----------------------------------------------------
// Pre-check State for Run IDs
// ----------------------------------------------------
const isPrechecking = ref(false)
const precheckResult = ref<RunPrecheckResult | null>(null)

// ----------------------------------------------------
// Input Handlers
// ----------------------------------------------------
function onFileUpload(file: File | File[] | null | undefined) {
  const selected = Array.isArray(file) ? file[0] : file
  if (selected) {
    inputSourceMode.value = 'file'
    omexFile.value = selected
    omexUrl.value = ''
    selectedPlatformRun.value = null
    checkOmexCompatibility()
  } else {
    clearFile()
  }
}

function resetArchiveState() {
  compatibilityRequest++
  checkingCompatibility.value = false
  compatibilityResponse.value = null
  compatibilityError.value = null
  compatibleSimulators.value = []
  historicalGroups.value = {}
  historicalRunsError.value = null
  step2Mode.value = 'solvers'
}

function clearFile() {
  omexFile.value = null
  resetArchiveState()
}

function onUrlSubmit() {
  if (!omexUrl.value.trim()) return
  inputSourceMode.value = 'url'
  omexFile.value = null
  selectedPlatformRun.value = null
  checkOmexCompatibility()
}

function clearUrl() {
  omexUrl.value = ''
  resetArchiveState()
}

function onPlatformRunSelected(run: {
  id: string
  name: string
  simulator: string
  simulatorVersion: string
  downloadUrl: string
}) {
  inputSourceMode.value = 'run'
  selectedPlatformRun.value = run
  omexFile.value = null
  omexUrl.value = ''
  checkOmexCompatibility()
}

function clearPlatformRun() {
  selectedPlatformRun.value = null
  resetArchiveState()
}

watch(inputSourceMode, (newMode) => {
  if (newMode === 'file') {
    if (omexUrl.value || selectedPlatformRun.value) {
      omexUrl.value = ''
      selectedPlatformRun.value = null
      if (omexFile.value) {
        checkOmexCompatibility()
      } else {
        clearFile()
      }
    }
  } else if (newMode === 'url') {
    if (omexFile.value || selectedPlatformRun.value) {
      omexFile.value = null
      selectedPlatformRun.value = null
      if (omexUrl.value.trim()) {
        checkOmexCompatibility()
      } else {
        clearUrl()
      }
    }
  } else if (newMode === 'run') {
    if (omexFile.value || omexUrl.value) {
      omexFile.value = null
      omexUrl.value = ''
      if (selectedPlatformRun.value) {
        checkOmexCompatibility()
      } else {
        clearPlatformRun()
      }
    }
  }
})

// ----------------------------------------------------
// Compatibility Check API Roundtrip
// ----------------------------------------------------
async function checkOmexCompatibility() {
  const hasFile = Boolean(omexFile.value)
  const urlToUse = selectedPlatformRun.value?.downloadUrl || omexUrl.value.trim()
  if (!hasFile && !urlToUse) return

  const request = ++compatibilityRequest
  checkingCompatibility.value = true
  compatibilityError.value = null
  compatibilityResponse.value = null
  compatibleSimulators.value = []

  try {
    let res: CompatibilityResponse
    if (hasFile) {
      const formData = new FormData()
      formData.append('uploaded_file', omexFile.value!)
      res = await $fetch<CompatibilityResponse>(`${config.public.api_url}/compatibility/check`, {
        method: 'POST',
        body: formData
      })
    } else {
      res = await $fetch<CompatibilityResponse>(`${config.public.api_url}/compatibility/check`, {
        method: 'POST',
        params: {
          archive_url: urlToUse
        }
      })
    }
    if (request !== compatibilityRequest) return

    compatibilityResponse.value = res

    if (res && res.eligible_simulators) {
      compatibleSimulators.value = res.eligible_simulators.map(elig => ({
        id: elig.id,
        name: elig.name,
        exact: elig.exact,
        versions: elig.versions || [],
        selectedVersion: elig.versions?.[0] || '',
        selected: true
      }))
    } else {
      compatibleSimulators.value = []
    }

    // Query platform runs for this archive to populate historical runs tree
    await fetchHistoricalRunsForArchive()

    // Ask the server for earlier verifications of this archive (fills the banner)
    if (request === compatibilityRequest && res?.omex_id) {
      fetchArchiveVerifications(res.omex_id)
    }
  } catch (err: any) {
    if (request !== compatibilityRequest) return
    compatibilityError.value = err?.data?.detail || err?.message || 'Could not verify archive compatibility.'
    compatibleSimulators.value = []
  } finally {
    if (request === compatibilityRequest) {
      checkingCompatibility.value = false
    }
  }
}

// ----------------------------------------------------
// Query Historical Platform Runs for Archive
// ----------------------------------------------------
async function fetchHistoricalRunsForArchive() {
  isSearchingHistoricalRuns.value = true
  historicalRunsError.value = null
  historicalGroups.value = {}

  try {
    const res: any = await $fetch(`${config.public.api_url}/simulations/runs`, {
      method: 'POST',
      body: {
        type: 'all',
        pagination: { page: 1, perPage: 100 },
        sort: { id: 'submitted', direction: 'desc' },
        filters: []
      }
    })

    if (res && res.runs && res.runs.length > 0) {
      const allRuns: any[] = res.runs
      const targetName = (omexFile.value?.name || selectedPlatformRun.value?.name || '').toLowerCase()
      const targetRunId = selectedPlatformRun.value?.id || ''

      const runsToGroup = allRuns.filter((r) => {
        if (targetRunId && (r.biosimulationsRunId === targetRunId || r.id === targetRunId)) return true
        return !!(targetName && r.name && r.name.toLowerCase().includes(targetName.replace(/\.omex$/, '')));
      })

      const groups: Record<string, HistoricalSimulatorGroup> = {}
      runsToGroup.forEach((r) => {
        const simId = (r.simulator || 'unknown').toLowerCase()
        const simName = normalizeSimulatorName(r.simulator || 'unknown')
        const ver = r.simulatorVersion || 'latest'
        const runId = r.biosimulationsRunId || r.id

        if (!groups[simId]) {
          groups[simId] = {
            simulatorId: simId,
            simulatorName: simName,
            versions: {},
            collapsed: false
          }
        }

        if (!groups[simId].versions[ver]) {
          groups[simId].versions[ver] = {
            version: ver,
            runs: [],
            collapsed: false
          }
        }

        groups[simId].versions[ver].runs.push({
          id: runId,
          biosimulationsRunId: r.biosimulationsRunId || r.id,
          name: r.name || runId,
          simulator: r.simulator,
          simulatorVersion: ver,
          status: r.status || 'SUCCEEDED',
          submitted: r.submitted || '',
          selected: false
        })
      })

      historicalGroups.value = groups
    }
  } catch (err: any) {
    historicalRunsError.value = err?.data?.detail || err?.message || 'Could not query historical platform runs for this archive.'
  } finally {
    isSearchingHistoricalRuns.value = false
  }
}

const selectedHistoricalRunIds = computed(() => {
  const ids: string[] = []
  Object.values(historicalGroups.value).forEach((simGroup) => {
    Object.values(simGroup.versions).forEach((verGroup) => {
      verGroup.runs.forEach((run) => {
        if (run.selected) {
          ids.push(run.id)
        }
      })
    })
  })
  return ids
})

const totalHistoricalRunsCount = computed(() => {
  let count = 0
  Object.values(historicalGroups.value).forEach((simGroup) => {
    Object.values(simGroup.versions).forEach((verGroup) => {
      count += verGroup.runs.length
    })
  })
  return count
})

// ----------------------------------------------------
// Server-Backed Archive Verification Matcher
// ----------------------------------------------------
// The listing has no total, so a full page reads as "N+"
const ARCHIVE_LOOKUP_LIMIT = 100
const archiveVerifications = ref<{ hash: string, count: number, hasMore: boolean } | null>(null)

const archiveVerificationCount = computed<string | null>(() => {
  const found = archiveVerifications.value
  if (!found || found.count === 0 || found.hash !== compatibilityResponse.value?.omex_id) return null
  return found.hasMore ? `${found.count}+` : String(found.count)
})

async function fetchArchiveVerifications(hash: string) {
  archiveVerifications.value = null
  try {
    const page = await fetchVerificationPage({ limit: ARCHIVE_LOOKUP_LIMIT, omexHash: hash })
    if (compatibilityResponse.value?.omex_id !== hash) return
    archiveVerifications.value = { hash, count: page.rows.length, hasMore: page.nextCursor !== null }
  } catch {
    // The banner is only a hint; the lookup tab reports listing errors
  }
}

function viewPastReportsForCurrentHash() {
  const hash = compatibilityResponse.value?.omex_id
  if (hash) {
    setArchiveFilter(hash)
  }
}

// ----------------------------------------------------
// Submit Actions
// ----------------------------------------------------
async function submitOmexVerification() {
  const hasFile = Boolean(omexFile.value)
  const urlToUse = selectedPlatformRun.value?.downloadUrl || omexUrl.value.trim()

  if (!hasFile && !urlToUse) {
    submissionError.value = 'Please provide an OMEX archive (upload local file, enter URL, or select from platform runs).'
    return
  }

  const selectedSims = compatibleSimulators.value.filter(s => s.selected)
  if (selectedSims.length < 2) {
    submissionError.value = 'Please select at least 2 compatible simulators for cross-verification.'
    return
  }

  isSubmitting.value = true
  submissionError.value = null

  try {
    let fileToSend: File | Blob | null = omexFile.value

    // If file is from URL or platform run, fetch blob
    if (!fileToSend && urlToUse) {
      try {
        const resp = await fetch(urlToUse)
        if (!resp.ok) {
          throw new Error(`Could not download archive from URL (status ${resp.status}).`)
        }
        fileToSend = await resp.blob()
      } catch (dlErr: any) {
        throw new Error(`Could not retrieve archive file from the provided URL: ${dlErr.message || 'Please ensure the link is publicly accessible.'}`)
      }
    }

    if (!fileToSend) {
      throw new Error('No valid archive data available to submit.')
    }

    const formData = new FormData()
    formData.append('uploaded_file', fileToSend, omexFile.value?.name || selectedPlatformRun.value?.name || 'model.omex')

    const queryParams = new URLSearchParams()
    queryParams.set('user_description', toleranceSettings.user_description || 'omex-verify')
    queryParams.set('rel_tol', String(toleranceSettings.rel_tol))
    queryParams.set('abs_tol_min', String(toleranceSettings.abs_tol_min))
    queryParams.set('abs_tol_scale', String(toleranceSettings.abs_tol_scale))
    queryParams.set('include_outputs', String(toleranceSettings.include_outputs))

    selectedSims.forEach((sim) => {
      const spec = sim.selectedVersion ? `${sim.id}:${sim.selectedVersion}` : sim.id
      queryParams.append('simulators', spec)
    })

    const url = `${config.public.api_url}/verify/omex?${queryParams.toString()}`
    const res = await $fetch<VerifyWorkflowOutput>(url, {
      method: 'POST',
      body: formData
    })

    if (res && res.workflow_id) {
      // Track all submitted simulators so client can detect any dropped/omitted by backend
      const submitted = selectedSims.map(s => ({
        id: s.id,
        name: s.name,
        version: s.selectedVersion
      }))
      submittedSimulatorsMap.value[res.workflow_id] = submitted
      try {
        sessionStorage.setItem(`verify_submitted_${res.workflow_id}`, JSON.stringify(submitted))
      } catch {
        // Ignore sessionStorage write errors
      }

      // Slide user over to Past Verifications tab and open newly created record in modal
      fetchPastVerifications(true)
      await openWorkflowReport(res.workflow_id, { output: res })
    }
  } catch (err: any) {
    submissionError.value = err?.data?.detail || err?.message || 'Failed to submit OMEX verification request.'
  } finally {
    isSubmitting.value = false
  }
}

async function submitRunsVerification(customRunIds?: string[]) {
  const idsToVerify = customRunIds && customRunIds.length > 0
    ? customRunIds
    : selectedHistoricalRunIds.value

  if (idsToVerify.length < 2) {
    submissionError.value = 'Please select at least 2 simulation run IDs to compare.'
    return
  }

  isSubmitting.value = true
  isPrechecking.value = true
  submissionError.value = null

  try {
    // Step 1: Automatic Pre-check
    const apiUrl = config.public.legacy_api_url
    const result = await precheckRunIds(idsToVerify, apiUrl)
    isPrechecking.value = false

    if (!result.can_proceed || result.valid_runs.length < 2) {
      submissionError.value = result.warning_message || 'Fewer than 2 valid simulation runs could be verified. Please check run IDs.'
      isSubmitting.value = false
      return
    }

    // Step 2: Submit verified subset to /verify/runs
    const idsToSubmit = result.valid_runs
    const queryParams = new URLSearchParams()
    queryParams.set('user_description', toleranceSettings.user_description || 'runs-verify')
    queryParams.set('rel_tol', String(toleranceSettings.rel_tol))
    queryParams.set('abs_tol_min', String(toleranceSettings.abs_tol_min))
    queryParams.set('abs_tol_scale', String(toleranceSettings.abs_tol_scale))
    queryParams.set('include_outputs', String(toleranceSettings.include_outputs))
    if (compatibilityResponse.value?.omex_id) {
      queryParams.set('omex_hash', compatibilityResponse.value.omex_id)
    }
    idsToSubmit.forEach(id => queryParams.append('biosimulations_run_ids', id))

    const url = `${config.public.api_url}/verify/runs?${queryParams.toString()}`
    const res = await $fetch<VerifyWorkflowOutput>(url, {
      method: 'POST'
    })

    if (res && res.workflow_id) {
      try {
        sessionStorage.setItem(`verify_precheck_${res.workflow_id}`, JSON.stringify(result))
      } catch {
        // Ignore sessionStorage write errors
      }

      // Slide user over to Past Verifications tab and open newly created record in modal
      fetchPastVerifications(true)
      await openWorkflowReport(res.workflow_id, { output: res, precheck: result })
    }
  } catch (err: any) {
    submissionError.value = err?.data?.detail || err?.message || 'Failed to submit runs verification request.'
  } finally {
    isSubmitting.value = false
    isPrechecking.value = false
  }
}

// ----------------------------------------------------
// Tab 2: Past Report Lookup / Direct Workflow Load & Catalog
// ----------------------------------------------------
const lookupIdInput = ref<string>('')
const catalogCardRef = ref<any>(null)
const PAST_PAGE_SIZE = 50
const pastVerifications = ref<VerificationListItem[]>([])
const pastNextCursor = ref<string | null>(null)
const isLoadingPastIds = ref<boolean>(false)
const isLoadingMorePastIds = ref<boolean>(false)
const pastIdsError = ref<string | null>(null)
const pastSearchQuery = ref<string>('')
const pastTypeFilter = ref<'all' | VerificationType>('all')
// Archive filter sent to the server as omex_hash; follows route.query.omex_hash
const pastHashFilter = ref<string | null>(null)
// False when the server lists bare IDs, so it can neither describe rows nor filter by archive
const pastListingSummaries = ref<boolean>(true)

// Whether the direct lookup input holds an OMEX archive hash rather than a workflow ID
const isLookupHash = computed(() => isOmexHash(lookupIdInput.value))

// Whether the search query in the catalog toolbar is a full 32-character OMEX archive hash
const isPastSearchQueryHash = computed(() => isOmexHash(pastSearchQuery.value))

// Narrows the loaded rows by verification run ID or OMEX hash; filtering by archive on the full database happens on the server
const filteredPastVerifications = computed(() => {
  return pastVerifications.value.filter(item =>
    matchesVerificationFilter(item, pastSearchQuery.value, pastTypeFilter.value)
  )
})

function resetLookupFilters() {
  lookupIdInput.value = ''
  pastSearchQuery.value = ''
  pastTypeFilter.value = 'all'
  pastIdsError.value = null
  const hadArchiveFilter = Boolean(pastHashFilter.value)
  pastHashFilter.value = null
  if (hadArchiveFilter) {
    pastVerifications.value = []
    pastNextCursor.value = null
  }
}

// The open report's type: from its listing row when loaded, else from its ID
const activeVerifyType = computed<VerificationType | null>(() => {
  const id = activeWorkflowId.value
  if (!id) return null
  return verificationTypeOf(pastVerifications.value.find(item => item.workflow_id === id) ?? { workflow_id: id })
})

function getVerificationType(type?: VerificationType | null): { label: string; icon: string; color: 'primary' | 'secondary' | 'neutral' } {
  if (type === 'omex') {
    return { label: 'OMEX Archive', icon: 'i-lucide-archive', color: 'primary' }
  }
  if (type === 'runs') {
    return { label: 'Multi-Run', icon: 'i-lucide-layers', color: 'secondary' }
  }
  return { label: 'Workflow', icon: 'i-lucide-hash', color: 'neutral' }
}

// The listing records terminal statuses only; null means the server hasn't seen the run finish
function getStatusBadge(status?: VerifyWorkflowStatus | null): { label: string; color: 'success' | 'error' | 'warning' | 'neutral' } {
  switch (status) {
    case 'COMPLETED':
      return { label: 'Completed', color: 'success' }
    case 'FAILED':
      return { label: 'Failed', color: 'error' }
    case 'RUN_ID_NOT_FOUND':
      return { label: 'Run not found', color: 'error' }
    case 'PENDING':
    case 'IN_PROGRESS':
      return { label: 'In progress', color: 'warning' }
    default:
      return { label: 'Unknown', color: 'neutral' }
  }
}

function formatCreated(created?: string | null): string | null {
  if (!created) return null
  const date = parseServerTimestamp(created)
  return Number.isNaN(date.getTime()) ? null : date.toLocaleString()
}

function copyWorkflowId(id?: string | null) {
  if (!id) return
  copy(id)
  toast.add({
    title: 'ID Copied',
    description: `Run ID ${id} copied to clipboard.`,
    color: 'success',
    icon: 'i-lucide-check'
  })
}

function copyPermalink(id?: string | null) {
  if (!id) return
  const origin = typeof window !== 'undefined' ? window.location.origin : ''
  const url = `${origin}/utilities/verify-model?tab=lookup&workflow_id=${id}`
  copy(url)
  toast.add({
    title: 'Permalink Copied',
    description: 'Direct link to verification run copied to clipboard.',
    color: 'success',
    icon: 'i-lucide-check'
  })
}

async function fetchVerificationPage(options: { limit: number, cursor?: string | null, omexHash?: string | null }) {
  const params: Record<string, string | number> = { limit: options.limit }
  if (options.cursor) params.cursor = options.cursor
  if (options.omexHash) params.omex_hash = options.omexHash
  const res = await $fetch<VerificationIdsResponse>(`${config.public.api_url}/verification_ids`, { params })
  return parseVerificationListing(res, options.omexHash ?? null)
}

// Bumped by every reset, so a page requested before it can't land in the new list
let pastListGeneration = 0

async function fetchPastVerifications(reset = true) {
  if (reset) {
    pastListGeneration++
    pastNextCursor.value = null
    isLoadingPastIds.value = true
    isLoadingMorePastIds.value = false
    pastIdsError.value = null
  } else {
    if (!pastNextCursor.value) return
    isLoadingMorePastIds.value = true
  }
  const generation = pastListGeneration

  try {
    const page = await fetchVerificationPage({
      limit: PAST_PAGE_SIZE,
      cursor: reset ? null : pastNextCursor.value,
      omexHash: pastHashFilter.value
    })
    if (generation !== pastListGeneration) return

    if (reset) {
      pastVerifications.value = page.rows
    } else {
      const loaded = new Set(pastVerifications.value.map(item => item.workflow_id))
      pastVerifications.value = [...pastVerifications.value, ...page.rows.filter(item => !loaded.has(item.workflow_id))]
    }
    pastNextCursor.value = page.nextCursor
    pastListingSummaries.value = page.summaries
  } catch (err: any) {
    if (generation !== pastListGeneration) return
    pastIdsError.value = err?.data?.detail || err?.message || 'Failed to load past verifications from server.'
  } finally {
    if (generation === pastListGeneration) {
      isLoadingPastIds.value = false
      isLoadingMorePastIds.value = false
    }
  }
}

function loadMorePastVerifications() {
  if (!pastNextCursor.value || isLoadingMorePastIds.value || isLoadingPastIds.value) return
  fetchPastVerifications(false)
}

const isHistoricalModalOpen = ref(false)
const isHistoricalModalFullscreen = ref(false)

// Everything derived from one report; switching reports must carry none of it over
function resetReportState() {
  workflowOutput.value = null
  precheckResult.value = null
  selectedI.value = 0
  selectedJ.value = 1
  activeVariable.value = ''
}

function readSavedPrecheck(workflowId: string): RunPrecheckResult | null {
  try {
    const saved = sessionStorage.getItem(`verify_precheck_${workflowId}`)
    return saved ? JSON.parse(saved) : null
  } catch {
    // Ignore sessionStorage read errors
    return null
  }
}

interface InitialReport {
  output?: VerifyWorkflowOutput
  precheck?: RunPrecheckResult
}

// Shows a report without navigating; route watchers and onMounted call this directly
function showWorkflow(id: string, initial: InitialReport = {}) {
  const wId = id.trim()
  if (!wId) return
  if (wId !== activeWorkflowId.value) {
    resetReportState()
    activeWorkflowId.value = wId
    precheckResult.value = initial.precheck ?? readSavedPrecheck(wId)
  }
  if (initial.output) {
    workflowOutput.value = initial.output
  }
  lookupIdInput.value = wId
  isHistoricalModalOpen.value = true
}

// User-facing action to open a report: activates lookup tab and preserves omex_hash if present
async function openWorkflowReport(id: string, initial: InitialReport = {}) {
  const wId = id.trim()
  if (!wId) return
  showWorkflow(wId, initial)
  activeMode.value = 'lookup'
  const q: Record<string, string> = { tab: 'lookup', workflow_id: wId }
  if (pastHashFilter.value) {
    q.omex_hash = pastHashFilter.value
  }
  await router.push({ query: q })
}

// VerificationStatus reports every poll; ignore answers for a report the user has left
function onWorkflowOutput(output: VerifyWorkflowOutput) {
  if (output.workflow_id === activeWorkflowId.value) {
    workflowOutput.value = output
  }
}

// ----------------------------------------------------
// Archive Filtering Controller (Tab 2)
// ----------------------------------------------------
async function setArchiveFilter(rawHash: string | null) {
  const hash = rawHash?.trim().toLowerCase() || null
  if (hash && !OMEX_HASH_PATTERN.test(hash)) {
    toast.add({
      title: 'Invalid Archive Identifier',
      description: 'The specified archive identifier must be a valid 32-character hash.',
      color: 'warning',
      icon: 'i-lucide-alert-triangle'
    })
    return
  }

  // Update filter & search state
  pastSearchQuery.value = ''
  pastTypeFilter.value = 'all'
  pastHashFilter.value = hash
  lookupIdInput.value = hash || ''
  activeMode.value = 'lookup'

  // Sync route
  const q: Record<string, string> = { tab: 'lookup' }
  if (hash) {
    q.omex_hash = hash
  }
  await router.push({ query: q })

  // Trigger server fetch with omex_hash
  await fetchPastVerifications(true)
}

function clearHashFilter() {
  setArchiveFilter(null)
}

function filterByCardHash(hash?: string | null) {
  if (hash) {
    setArchiveFilter(hash)
  }
}

function applyHashFilterFromSearch() {
  setArchiveFilter(pastSearchQuery.value)
}

function handleCatalogSearchEnter() {
  const query = pastSearchQuery.value.trim().toLowerCase()
  if (isOmexHash(query)) {
    applyHashFilterFromSearch()
  }
}

async function handleDirectLookup() {
  const input = lookupIdInput.value.trim()
  if (!input) return
  if (isOmexHash(input)) {
    await setArchiveFilter(input)
    nextTick(() => {
      const el = catalogCardRef.value?.$el || catalogCardRef.value
      el?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
    })
  } else {
    await openWorkflowReport(input)
  }
}

// ----------------------------------------------------
// Tab & Route Synchronization
// ----------------------------------------------------
watch(activeMode, (mode) => {
  if (mode === 'verify') {
    // Exclusively for new verification setup: clear historic run state, filters, and URL params
    isHistoricalModalOpen.value = false
    isHistoricalModalFullscreen.value = false
    resetReportState()
    activeWorkflowId.value = null
    resetLookupFilters()

    if (route.query.tab || route.query.workflow_id || route.query.run_id || route.query.omex_hash || route.query.omex_id) {
      router.replace({ query: {} })
    }
  } else {
    // Exclusively for discovering and inspecting past runs
    if (route.query.tab !== 'lookup') {
      const q: Record<string, string> = { tab: 'lookup' }
      if (pastHashFilter.value) q.omex_hash = pastHashFilter.value
      router.replace({ query: q })
    }
    if (!pastHashFilter.value && pastVerifications.value.length === 0 && !isLoadingPastIds.value) {
      fetchPastVerifications(true)
    }
  }
})

watch(isHistoricalModalOpen, (isOpen) => {
  if (!isOpen) {
    isHistoricalModalFullscreen.value = false
    resetReportState()
    activeWorkflowId.value = null
    if (activeMode.value === 'lookup' && (route.query.workflow_id || route.query.run_id)) {
      const q: Record<string, string> = { tab: 'lookup' }
      if (pastHashFilter.value) q.omex_hash = pastHashFilter.value
      router.push({ query: q })
    }
  }
})

// Route watchers: only react to external / browser navigation (e.g. Back/Forward)
watch(() => route.query.workflow_id, (newWId) => {
  if (newWId) {
    showWorkflow(String(newWId))
  } else {
    isHistoricalModalOpen.value = false
  }
})

watch(() => route.query.omex_hash, (newHash) => {
  const hash = newHash ? String(newHash).trim().toLowerCase() : null
  if (hash !== pastHashFilter.value) {
    if (hash) activeMode.value = 'lookup'
    pastHashFilter.value = hash
    lookupIdInput.value = hash || ''
    fetchPastVerifications(true)
  }
})


// ----------------------------------------------------
// Dashboard Active State & Calculations
// ----------------------------------------------------
const activeDataset = ref<string>('')
const selectedI = ref<number>(0)
const selectedJ = ref<number>(1)
const activeVariable = ref<string>('')

const availableDatasets = computed<string[]>(() => {
  const stats = workflowOutput.value?.workflow_results?.comparison_statistics
  if (!stats) return []
  return Object.keys(stats)
})

watch(availableDatasets, (dsList) => {
  if (dsList.length > 0 && (!activeDataset.value || !dsList.includes(activeDataset.value))) {
    activeDataset.value = dsList[0]!
  }
}, { immediate: true })

const currentMatrix = computed<ComparisonStatistics[][]>(() => {
  if (!activeDataset.value) return []
  return workflowOutput.value?.workflow_results?.comparison_statistics?.[activeDataset.value] || []
})

function normalizeSimulatorName(name: string): string {
  const map: Record<string, string> = {
    copasi: 'COPASI',
    amici: 'AMICI',
    tellurium: 'Tellurium',
    vcell: 'Virtual Cell',
    pysces: 'PySCeS'
  }
  return map[name.toLowerCase()] || (name.charAt(0).toUpperCase() + name.slice(1))
}

const activeRunInfos = computed<SimulationRunInfo[]>(() => {
  const runInfos = workflowOutput.value?.workflow_results?.sims_run_info
  if (!runInfos || runInfos.length === 0) return []
  const succeeded = runInfos.filter(info => info.biosim_sim_run.status === 'SUCCEEDED')
  return succeeded.length > 0 ? succeeded : runInfos
})

const simulatorsList = computed<SimulatorInfo[]>(() => {
  if (activeRunInfos.value.length > 0) {
    return activeRunInfos.value.map((info) => {
      const v = info.biosim_sim_run.simulator_version
      const rawName = v?.name || v?.id || info.biosim_sim_run.simulator || 'Solver'
      return {
        id: v?.id || info.biosim_sim_run.simulator || info.biosim_sim_run.id,
        name: normalizeSimulatorName(rawName),
        version: v?.version || undefined
      }
    })
  }

  if (currentMatrix.value.length > 0) {
    return currentMatrix.value.map((row, idx) => {
      const label = row[0]?.simulator_version_i || `Simulator ${idx + 1}`
      const [id, version] = label.split(':')
      return { id: id || label, name: normalizeSimulatorName(id || label), version }
    })
  }

  return []
})

const submittedSimulatorsMap = ref<Record<string, Array<{ id: string, name: string, version?: string }>>>({})

function getSubmittedSimulatorsForWorkflow(wId: string): Array<{ id: string, name: string, version?: string }> {
  if (submittedSimulatorsMap.value[wId] && submittedSimulatorsMap.value[wId]!.length > 0) {
    return submittedSimulatorsMap.value[wId]!
  }
  try {
    const saved = sessionStorage.getItem(`verify_submitted_${wId}`)
    if (saved) {
      const parsed = JSON.parse(saved)
      submittedSimulatorsMap.value[wId] = parsed
      return parsed
    }
  } catch {
    // Ignore storage errors
  }
  return []
}

const excludedSimulatorsList = computed<ExcludedSimulatorInfo[]>(() => {
  const list: ExcludedSimulatorInfo[] = []
  const seenIds = new Set<string>()

  // 1. From precheckResult (historical runs)
  if (precheckResult.value?.excluded_runs) {
    for (const exc of precheckResult.value.excluded_runs) {
      if (seenIds.has(exc.run_id)) continue
      seenIds.add(exc.run_id)
      const rawSim = exc.simulator || ''
      const [name, version] = rawSim.split(':')
      const formattedName = name && name !== 'unknown'
        ? normalizeSimulatorName(name)
        : `Run ${exc.run_id.slice(0, 8)}…`
      list.push({
        id: exc.run_id,
        name: formattedName,
        version: version || undefined,
        status: exc.status || 'FAILED',
        reason: exc.reason,
        runId: exc.run_id
      })
    }
  }

  // 2. Identify solvers submitted for this workflow that were omitted/dropped by the backend
  if (activeWorkflowId.value) {
    const submitted = getSubmittedSimulatorsForWorkflow(activeWorkflowId.value)
    if (submitted.length > 0) {
      const runInfos = workflowOutput.value?.workflow_results?.sims_run_info || []
      const succeededIds = new Set(runInfos.map((info) => {
        const v = info.biosim_sim_run.simulator_version
        return (v?.id || info.biosim_sim_run.simulator || '').toLowerCase()
      }))

      for (const sim of submitted) {
        if (!succeededIds.has(sim.id.toLowerCase())) {
          const key = `${sim.id}:${sim.version || ''}`
          if (seenIds.has(key)) continue
          seenIds.add(key)
          list.push({
            id: sim.id,
            name: sim.name || normalizeSimulatorName(sim.id),
            version: sim.version || undefined,
            status: 'DROPPED / FAILED',
            reason: `Simulator ${sim.name || sim.id} was selected and submitted for verification, but was omitted from comparison results. The simulation container likely failed during execution or did not generate compatible HDF5 output on the BioSimulations cluster.`
          })
        }
      }
    }
  }

  // 3. From sims_run_info (if any non-SUCCEEDED made it)
  const runInfos = workflowOutput.value?.workflow_results?.sims_run_info
  if (runInfos) {
    for (const info of runInfos) {
      if (info.biosim_sim_run.status !== 'SUCCEEDED') {
        const runId = info.biosim_sim_run.id
        if (seenIds.has(runId)) continue
        seenIds.add(runId)
        const v = info.biosim_sim_run.simulator_version
        const rawName = v?.name || (info.biosim_sim_run.simulator ? normalizeSimulatorName(info.biosim_sim_run.simulator) : `Run ${runId.slice(0, 8)}…`)
        list.push({
          id: runId,
          name: rawName,
          version: v?.version || undefined,
          status: info.biosim_sim_run.status || 'FAILED',
          reason: info.biosim_sim_run.error_message || `Simulation status is ${info.biosim_sim_run.status}`,
          runId: runId
        })
      }
    }
  }

  return list
})

const selectedPairStats = computed<ComparisonStatistics | undefined>(() => {
  return currentMatrix.value?.[selectedI.value]?.[selectedJ.value]
    || currentMatrix.value?.[selectedJ.value]?.[selectedI.value]
})

const selectedPairLabel = computed(() => {
  const simA = simulatorsList.value[selectedI.value]
  const simB = simulatorsList.value[selectedJ.value]
  if (!simA || !simB) return ''
  return `${simA.name} vs ${simB.name}`
})

const runIId = computed(() => {
  return activeRunInfos.value[selectedI.value]?.biosim_sim_run.id
})

const runJId = computed(() => {
  return activeRunInfos.value[selectedJ.value]?.biosim_sim_run.id
})

const variableComparisonRows = computed(() => {
  if (!selectedPairStats.value) return []

  const simNames = simulatorsList.value.map(s => s.name)
  return buildVariableComparisonRows(
    selectedPairStats.value,
    runIId.value,
    runJId.value,
    workflowOutput.value?.workflow_results?.sim_run_data,
    simNames,
    currentMatrix.value
  )
})

const concordanceSummary = computed(() => summarizeConcordance(currentMatrix.value))

const concordanceClass = computed(() => {
  const { percentage, concordantCount, totalCount, comparedPairs, totalPairs } = concordanceSummary.value
  if (percentage === null) return 'text-neutral-400'
  // Green, like a heatmap cell, only when every variable of every solver pair was compared and agreed
  if (concordantCount === totalCount && comparedPairs === totalPairs) return 'text-emerald-600'
  return percentage >= 70 ? 'text-amber-500' : 'text-rose-600'
})

const concordanceNotice = computed<string | null>(() => {
  const { percentage, totalPairs } = concordanceSummary.value
  if (percentage !== null) return null
  return totalPairs === 0
    ? 'Fewer than two solvers produced results for this dataset, so nothing could be compared.'
    : 'None of the solver pairs could be compared for this dataset. Select a heatmap cell to see why.'
})

const totalOutlierCount = computed(() => {
  return variableComparisonRows.value.filter(r => r.outlier_simulators && r.outlier_simulators.length > 0).length
})

function onSelectHeatmapPair(i: number, j: number) {
  selectedI.value = i
  selectedJ.value = j
}

const modalPlotSectionRef = ref<HTMLElement | null>(null)
function onVisualizeVariable(varName: string) {
  activeVariable.value = varName
  nextTick(() => {
    if (modalPlotSectionRef.value) {
      modalPlotSectionRef.value.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  })
}

// On mount: check URL for workflow_id, omex_hash, and tab
onMounted(() => {
  const initialHash = (route.query.omex_hash || route.query.omex_id) as string | undefined
  const initialWId = (route.query.workflow_id || route.query.run_id) as string | undefined

  if (initialHash && isOmexHash(String(initialHash))) {
    setArchiveFilter(String(initialHash))
  } else if (initialWId) {
    activeMode.value = 'lookup'
    showWorkflow(String(initialWId))
    fetchPastVerifications(true)
  } else if (route.query.tab === 'lookup') {
    activeMode.value = 'lookup'
    fetchPastVerifications(true)
  }
})
</script>

<template>
  <div class="min-h-screen bg-neutral-50 py-8 px-4 sm:px-6 lg:px-8">
    <div class="max-w-7xl mx-auto space-y-8">
      <!-- Breadcrumbs & Header -->
      <div>
        <UBreadcrumb :items="breadcrumbs" class="mb-3">
          <template #separator>
            <span class="mx-1 text-neutral-400">/</span>
          </template>
        </UBreadcrumb>

        <div class="flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
          <div>
            <h1 class="text-3xl font-bold tracking-tight text-neutral-900 flex items-center gap-3">
              <UIcon name="i-lucide-shield-check" class="size-8 text-primary" />
              Model Verification Hub
            </h1>
            <p class="mt-1 text-sm text-neutral-600 max-w-3xl">
              Cross-verify SBML/SED-ML models across multiple independent simulation solvers
              (AMICI, COPASI, PySCeS, Tellurium, VCell). Quantify numerical equivalence, detect solver-specific outliers, and inspect time-series concordance.
            </p>
          </div>
        </div>
      </div>

      <!-- 2-Tab Navigation -->
      <UTabs
        v-model="activeMode"
        :items="tabItems"
        :content="false"
        color="primary"
        variant="link"
        class="border-b border-neutral-200"
      />

      <!-- Tab 1: Unified Verification Flow -->
      <div v-if="activeMode === 'verify'" class="space-y-6">
        <!-- ISLAND 1: Step 1: Provide COMBINE/OMEX Archive -->
        <UCard class="shadow-sm">
          <template #header>
            <div>
              <h2 class="text-base sm:text-lg font-semibold text-neutral-900 flex items-center gap-2">
                <span class="size-6 rounded-full bg-primary/10 text-primary flex items-center justify-center text-xs font-bold">1</span>
                Provide COMBINE/OMEX Archive
              </h2>
              <p class="text-xs text-neutral-500 mt-0.5">
                Upload a local archive, specify a public URL, or select from completed runs. Compatibility is evaluated automatically.
              </p>
            </div>
          </template>

          <div class="space-y-6">

          <!-- Input Mode Switcher -->
          <div class="space-y-3">
            <label class="block text-xs font-semibold text-neutral-600 uppercase tracking-wider">
              Input Method
            </label>
            <UTabs
              v-model="inputSourceMode"
              :items="inputSourceModes"
              class="w-full"
            />
          </div>

          <!-- Mode 1: File Upload -->
          <div v-if="inputSourceMode === 'file'" class="pt-1">
            <UFileUpload
              v-model="omexFile"
              accept=".omex,.zip"
              layout="list"
              icon="i-lucide-upload-cloud"
              label="Drop COMBINE/OMEX archive here"
              class="w-full min-h-40"
              :disabled="checkingCompatibility || isSubmitting"
              @update:model-value="onFileUpload"
            >
              <template #description>
                <div class="flex flex-col items-center gap-1 mt-1">
                  <span class="text-xs text-neutral-500">or click to browse from your device</span>
                  <div class="flex flex-wrap items-center justify-center gap-1.5 mt-1.5">
                    <span class="text-xs text-neutral-400">Accepted formats:</span>
                    <UBadge
                      v-for="ext in ['.omex', '.zip']"
                      :key="ext"
                      size="md"
                      variant="subtle"
                      color="neutral"
                    >
                      {{ ext }}
                    </UBadge>
                  </div>
                </div>
              </template>
            </UFileUpload>
          </div>

          <!-- Mode 2: Public URL -->
          <div v-else-if="inputSourceMode === 'url'" class="space-y-3 pt-1">
            <div class="space-y-1.5">
              <label class="block text-sm font-medium text-neutral-700">
                Public Archive URL
              </label>
              <div class="flex gap-2">
                <UInput
                  v-model="omexUrl"
                  placeholder="https://example.org/biomodel.omex"
                  class="flex-1 font-mono text-xs"
                  icon="i-lucide-globe"
                  :disabled="checkingCompatibility || isSubmitting"
                  @keydown.enter.prevent="onUrlSubmit"
                />
                <UButton
                  variant="soft"
                  color="primary"
                  icon="i-lucide-check-circle"
                  :disabled="!omexUrl.trim() || checkingCompatibility || isSubmitting"
                  label="Evaluate URL"
                  @click="onUrlSubmit"
                />
                <UButton
                  v-if="omexUrl"
                  variant="ghost"
                  color="neutral"
                  icon="i-lucide-x"
                  title="Clear URL"
                  :disabled="checkingCompatibility || isSubmitting"
                  @click="clearUrl"
                />
              </div>
              <p class="text-xs text-neutral-500">
                Ensure the URL points directly to a publicly accessible COMBINE/OMEX or SED-ML archive.
              </p>
            </div>
          </div>

          <!-- Mode 3: Platform Run -->
          <div v-else-if="inputSourceMode === 'run'" class="space-y-3 pt-1">
            <div class="space-y-1.5">
              <label class="block text-sm font-medium text-neutral-700">
                Platform Run Archive
              </label>
              <div v-if="!selectedPlatformRun" class="flex flex-col items-start gap-2">
                <p class="text-xs text-neutral-500">
                  Pick a completed run from the platform database to extract and evaluate its archive.
                </p>
                <UButton
                  color="primary"
                  variant="soft"
                  icon="i-lucide-search"
                  label="Browse Platform Runs"
                  :disabled="checkingCompatibility || isSubmitting"
                  @click="isRunPickerModalOpen = true"
                />
              </div>
              <div v-else class="flex items-center gap-3">
                <div class="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-primary/10 border border-primary/20 text-xs font-medium text-primary">
                  <UIcon name="i-lucide-check-circle-2" class="size-4 shrink-0 text-primary" />
                  <span class="truncate max-w-[240px] font-mono">{{ selectedPlatformRun.name }}</span>
                  <span class="text-neutral-400 text-[10px]">({{ selectedPlatformRun.simulator }})</span>
                  <UButton
                    variant="ghost"
                    color="neutral"
                    size="xs"
                    icon="i-lucide-x"
                    title="Remove selected run"
                    class="p-0.5 ml-1"
                    @click="clearPlatformRun"
                  />
                </div>
                <UButton
                  variant="link"
                  color="primary"
                  size="xs"
                  label="Choose a different run"
                  class="p-0 text-xs"
                  @click="isRunPickerModalOpen = true"
                />
              </div>
            </div>
          </div>

          <!-- Compatibility Loading Indicator -->
          <UAlert
            v-if="checkingCompatibility"
            color="primary"
            variant="subtle"
            icon="i-svg-spinners:ring-resize"
            title="Analyzing SED-ML Models"
            description="Extracting algorithm KiSAO IDs and matching compatible solvers..."
          />

          <!-- Compatibility Error Notice -->
          <UAlert
            v-if="compatibilityError"
            color="warning"
            variant="subtle"
            icon="i-lucide-alert-triangle"
            title="Compatibility Notice"
            :description="compatibilityError"
            orientation="horizontal"
            :actions="[
              {
                label: 'Retry',
                color: 'warning',
                variant: 'soft',
                icon: 'i-lucide-rotate-cw',
                onClick: checkOmexCompatibility
              }
            ]"
          />

          <!-- Archive Compatibility Summary Pill & History (Island 1 Footer) -->
          <div v-if="compatibilityResponse && !checkingCompatibility" class="space-y-4 pt-2">
            <!-- Archive Compatibility Summary Pill -->
            <div
              class="p-3.5 bg-neutral-50 border border-neutral-200 rounded-lg flex flex-wrap items-center justify-between gap-3 text-xs"
            >
              <div class="flex flex-wrap items-center gap-3 text-neutral-600">
                <span class="inline-flex items-center gap-1 font-mono font-medium text-neutral-900">
                  <UIcon name="i-lucide-hash" class="size-3.5 text-primary" />
                  Hash: {{ compatibilityResponse.omex_id }}
                  <UButton trailing-icon="i-lucide-copy" variant="ghost" size="sm" @click="copy(compatibilityResponse.omex_id)" />
                </span>
                <span>•</span>
                <span v-if="compatibilityResponse.omex_content.simulations && compatibilityResponse.omex_content.simulations.length > 0">
                  Algorithm: <strong class="text-neutral-800">{{ compatibilityResponse.omex_content.simulations[0]?.algorithm.name || 'ODE Solver' }}</strong>
                  ({{ compatibilityResponse.omex_content.simulations[0]?.algorithm.id }})
                </span>
                <span>•</span>
                <span>
                  {{ compatibilityResponse.omex_content.sedml_files.length }} SED-ML File{{ compatibilityResponse.omex_content.sedml_files.length === 1 ? '' : 's' }}
                </span>
              </div>

              <UBadge color="success" variant="subtle" size="md">
                {{ compatibleSimulators.length }} Compatible Solvers Matched
              </UBadge>
            </div>

            <!-- Previous Verification Alert Banner -->
            <UAlert
              v-if="archiveVerificationCount"
              color="primary"
              variant="subtle"
              icon="i-lucide-history"
              title="Previous Verifications Found"
              :description="`This OMEX archive has ${archiveVerificationCount} verification run(s) recorded on the platform.`"
              orientation="horizontal"
              :actions="[
                {
                  label: 'View Past Reports',
                  color: 'primary',
                  variant: 'soft',
                  icon: 'i-lucide-external-link',
                  onClick: viewPastReportsForCurrentHash
                }
              ]"
            />
          </div>
          </div>
        </UCard>
        <!-- END ISLAND 1: Step 1 Card -->

        <!-- ISLAND 2: Step 2: Choose Verification Method (Dropdown) -->
        <Transition name="fade-slide">
          <UCard
            v-if="compatibilityResponse && !checkingCompatibility"
            class="shadow-sm"
          >
            <template #header>
              <div>
                <h2 class="text-base sm:text-lg font-semibold text-neutral-900 flex items-center gap-2">
                  <span class="size-6 rounded-full bg-primary/10 text-primary flex items-center justify-center text-xs font-bold">2</span>
                  Choose Verification Method
                </h2>
                <p class="text-xs text-neutral-500 mt-0.5">
                  Select your verification approach: execute new cross-simulations across solvers, or compare pre-computed historical runs for this archive.
                </p>
              </div>
            </template>

            <div class="max-w-2xl space-y-2">
              <label class="block text-xs font-semibold text-neutral-700">
                Verification Strategy
              </label>

              <USelect
                v-model="step2Mode"
                :items="verificationStrategyItems"
                size="md"
                class="w-full"
              />

              <!-- Contextual description -->
              <p v-if="step2Mode === 'solvers'" class="text-xs text-neutral-500 flex items-center gap-1.5 pt-0.5">
                <UIcon name="i-lucide-cpu" class="size-3.5 text-primary shrink-0" />
                <span>Simulate the archive concurrently in fresh solver containers and compare numerical trajectories.</span>
              </p>
              <p v-else-if="step2Mode === 'historical'" class="text-xs text-neutral-500 flex items-center gap-1.5 pt-0.5">
                <UIcon name="i-lucide-history" class="size-3.5 text-primary shrink-0" />
                <span>Verify existing HDF5 datasets generated by previously submitted simulation runs without re-executing.</span>
              </p>
            </div>
          </UCard>
        </Transition>
        <!-- END ISLAND 2: Step 2 Card -->

        <!-- ISLAND 3: Step 3: Selection Result Island -->
        <Transition name="fade-slide">
          <UCard
            v-if="compatibilityResponse && !checkingCompatibility"
            class="shadow-sm"
          >
            <template #header>
              <!-- 3A Header (Solvers) -->
              <div v-if="step2Mode === 'solvers'" class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                <div>
                  <h2 class="text-base sm:text-lg font-semibold text-neutral-900 flex items-center gap-2">
                    <span class="size-6 rounded-full bg-primary/10 text-primary flex items-center justify-center text-xs font-bold">3</span>
                    Select Solvers &amp; Versions for Cross-Verification
                  </h2>
                  <p class="text-xs text-neutral-500 mt-0.5">
                    Compatible solvers default to the latest release, with singleton version selection supported.
                  </p>
                </div>
                <span class="text-xs text-neutral-400 font-mono">
                  {{ compatibleSimulators.filter(s => s.selected).length }} of {{ compatibleSimulators.length }} selected
                </span>
              </div>

              <!-- 3B Header (Historical) -->
              <div v-else-if="step2Mode === 'historical'" class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                <div>
                  <h2 class="text-base sm:text-lg font-semibold text-neutral-900 flex items-center gap-2">
                    <span class="size-6 rounded-full bg-primary/10 text-primary flex items-center justify-center text-xs font-bold">3</span>
                    Select Historical Platform Runs to Compare
                  </h2>
                  <p class="text-xs text-neutral-500 mt-0.5">
                    Compare pre-computed HDF5 datasets directly from previous simulation runs on BioSimulations without re-executing simulations.
                  </p>
                </div>
                <div v-if="totalHistoricalRunsCount > 0" class="flex items-center gap-2">
                  <UBadge color="primary" variant="subtle" size="md">
                    {{ selectedHistoricalRunIds.length }} selected
                  </UBadge>
                  <UButton
                    v-if="selectedHistoricalRunIds.length > 0"
                    size="xs"
                    variant="ghost"
                    color="neutral"
                    label="Deselect All"
                    @click="Object.values(historicalGroups).forEach(sg => Object.values(sg.versions).forEach(vg => vg.runs.forEach(r => r.selected = false)))"
                  />
                </div>
              </div>
            </template>

            <!-- 3A: Solvers Selection & Tolerances -->
            <div v-if="step2Mode === 'solvers'" class="space-y-6">
              <!-- Empty State: No Compatible Solvers Found -->
              <UEmpty
                v-if="compatibleSimulators.length === 0"
                icon="i-lucide-cpu"
                title="No Compatible Solvers Detected"
                description="No simulation solvers on BioSimulations currently support this archive's algorithms or model format."
                variant="subtle"
              />

              <!-- Simulators Grid -->
              <div v-else class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
                <UCard
                  v-for="sim in compatibleSimulators"
                  :key="sim.id"
                  :class="[
                    'transition-all text-xs flex flex-col justify-between',
                    sim.selected
                      ? 'ring-2 ring-primary bg-primary-50/20 shadow-xs'
                      : 'opacity-70 hover:opacity-100'
                  ]"
                  variant="subtle"
                  :ui="{ body: 'p-3.5 flex flex-col justify-between h-full' }"
                >
                  <div>
                    <div class="flex items-start justify-between gap-2">
                      <UCheckbox
                        v-model="sim.selected"
                        :label="sim.name"
                        color="primary"
                        :ui="{ label: 'font-semibold text-neutral-900 cursor-pointer select-none text-xs' }"
                      />

                      <UBadge
                        size="md"
                        :color="sim.exact ? 'success' : 'primary'"
                        variant="subtle"
                      >
                        {{ sim.exact ? 'Exact' : 'Equivalent' }}
                      </UBadge>
                    </div>
                  </div>

                  <!-- Version Selector Dropdown -->
                  <div class="mt-3 pt-2.5 border-t border-neutral-100">
                    <label class="block text-[10px] uppercase font-semibold text-neutral-500 mb-1">
                      Solver Version:
                    </label>
                    <USelect
                      v-model="sim.selectedVersion"
                      :items="sim.versions.map((ver, vIdx) => ({ label: `v${ver} ${vIdx === 0 ? '(latest)' : ''}`, value: ver }))"
                      size="xs"
                      class="w-full font-mono text-xs"
                    />
                  </div>
                </UCard>
              </div>

              <!-- Tolerances & Submit Section (Solvers) -->
              <div class="pt-4 border-t border-neutral-100 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                <div class="flex items-center gap-4 text-xs">
                  <UCheckbox
                    v-model="toleranceSettings.include_outputs"
                    label="Store simulation solutions (Plotly trajectories)"
                  />
                  <button
                    type="button"
                    class="text-xs text-primary hover:underline flex items-center gap-1 cursor-pointer"
                    @click="showAdvancedSettings = !showAdvancedSettings"
                  >
                    <UIcon :name="showAdvancedSettings ? 'i-lucide-chevron-up' : 'i-lucide-chevron-down'" class="size-3.5" />
                    {{ showAdvancedSettings ? 'Hide Tolerances' : 'Adjust Tolerances' }}
                  </button>
                </div>

                <div class="flex items-center gap-3">
                  <UButton
                    color="primary"
                    size="md"
                    icon="i-lucide-play"
                    :loading="isSubmitting"
                    :disabled="(!omexFile && !omexUrl.trim() && !selectedPlatformRun) || compatibleSimulators.filter(s => s.selected).length < 2"
                    label="Verify Solvers"
                    @click="submitOmexVerification"
                  />
                </div>
              </div>

              <!-- Advanced Tolerances Drawer (Solvers) -->
              <div v-if="showAdvancedSettings" class="p-4 bg-neutral-50 rounded-lg grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs">
                <div>
                  <label class="block font-medium mb-1">Relative Tol (rtol)</label>
                  <UInput v-model.number="toleranceSettings.rel_tol" type="number" step="0.0001" size="xs" />
                </div>
                <div>
                  <label class="block font-medium mb-1">Min Absolute Tol (atol_min)</label>
                  <UInput v-model.number="toleranceSettings.abs_tol_min" type="number" step="0.0001" size="xs" />
                </div>
                <div>
                  <label class="block font-medium mb-1">Absolute Tol Scale (atol_scale)</label>
                  <UInput v-model.number="toleranceSettings.abs_tol_scale" type="number" step="0.00001" size="xs" />
                </div>
              </div>
            </div>

            <!-- 3B: Historical Runs Selection & Tolerances -->
            <div v-else-if="step2Mode === 'historical'" class="space-y-6">
              <!-- Searching Loading State -->
              <UEmpty
                v-if="isSearchingHistoricalRuns"
                loading
                title="Searching Platform Database"
                description="Locating completed runs matching this archive..."
                variant="subtle"
              />

              <!-- Error State: Failed to query historical runs -->
              <UAlert
                v-else-if="historicalRunsError"
                color="error"
                variant="subtle"
                icon="i-lucide-alert-circle"
                title="Failed to Retrieve Historical Runs"
                :description="historicalRunsError"
                orientation="horizontal"
                :actions="[
                  {
                    label: 'Retry Search',
                    color: 'error',
                    variant: 'soft',
                    icon: 'i-lucide-rotate-cw',
                    onClick: fetchHistoricalRunsForArchive
                  }
                ]"
              />

              <!-- Empty State: No Runs Found -->
              <UEmpty
                v-else-if="totalHistoricalRunsCount === 0"
                icon="i-lucide-history"
                title="No Historical Platform Runs Detected"
                description="No prior simulation runs were found on the platform for this archive. You can execute a new cross-verification workflow across compatible solvers."
                variant="subtle"
                :actions="[
                  {
                    label: 'Switch to Solvers Verification',
                    icon: 'i-lucide-cpu',
                    color: 'primary',
                    variant: 'soft',
                    onClick: () => { step2Mode = 'solvers' }
                  }
                ]"
              />

              <!-- Historical Runs Available -->
              <div v-else class="space-y-6">
                <!-- Hierarchical Tree Table: Simulator -> Version -> Runs -->
                <UCard variant="subtle" :ui="{ body: 'p-4 space-y-3' }">
                  <div
                    v-for="simGroup in historicalGroups"
                    :key="simGroup.simulatorId"
                    class="border border-neutral-200 rounded-lg overflow-hidden"
                  >
                    <!-- Simulator Header -->
                    <div
                      class="p-2.5 bg-neutral-50 flex items-center justify-between text-xs font-semibold cursor-pointer select-none"
                      @click="simGroup.collapsed = !simGroup.collapsed"
                    >
                      <div class="flex items-center gap-2">
                        <UIcon :name="simGroup.collapsed ? 'i-lucide-chevron-right' : 'i-lucide-chevron-down'" class="size-3.5 text-neutral-400" />
                        <span>{{ simGroup.simulatorName }}</span>
                      </div>
                      <span class="text-[11px] text-neutral-400 font-normal">
                        {{ Object.values(simGroup.versions).reduce((acc, v) => acc + v.runs.length, 0) }} runs
                      </span>
                    </div>

                    <!-- Versions & Runs -->
                    <div v-if="!simGroup.collapsed" class="divide-y divide-neutral-100">
                      <div
                        v-for="verGroup in simGroup.versions"
                        :key="verGroup.version"
                        class="p-2.5 space-y-2"
                      >
                        <div class="flex items-center justify-between text-[11px] font-mono text-neutral-500 font-medium">
                          <span class="flex items-center gap-1.5">
                            <UIcon name="i-lucide-tag" class="size-3 text-neutral-400" />
                            Version: v{{ verGroup.version }}
                          </span>
                          <span>{{ verGroup.runs.length }} run{{ verGroup.runs.length === 1 ? '' : 's' }}</span>
                        </div>

                        <div class="space-y-1.5 pl-3">
                          <div
                            v-for="run in verGroup.runs"
                            :key="run.id"
                            class="flex items-center justify-between p-2 rounded-lg hover:bg-neutral-50 transition-colors text-xs"
                          >
                            <div class="flex items-center gap-2.5">
                              <UCheckbox
                                v-model="run.selected"
                                color="primary"
                                size="sm"
                              />
                              <span class="font-mono font-medium text-neutral-800">
                                {{ run.id.slice(0, 14) }}…
                              </span>
                              <span class="text-neutral-400 text-[11px] truncate max-w-[200px]">
                                ({{ run.name }})
                              </span>
                            </div>

                            <div class="flex items-center gap-3">
                              <UBadge
                                :color="run.status === 'SUCCEEDED' ? 'success' : run.status === 'FAILED' ? 'error' : 'neutral'"
                                variant="subtle"
                                size="md"
                              >
                                {{ run.status }}
                              </UBadge>
                              <span v-if="run.submitted" class="text-[10px] text-neutral-400 font-mono">
                                {{ new Date(run.submitted).toLocaleDateString() }}
                              </span>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </UCard>

                <!-- Tolerances & Submit Section (Historical Runs) -->
                <div class="pt-4 border-t border-neutral-100 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                  <div class="flex items-center gap-4 text-xs">
                    <UCheckbox
                      v-model="toleranceSettings.include_outputs"
                      label="Store simulation solutions (Plotly trajectories)"
                    />
                    <button
                      type="button"
                      class="text-xs text-primary hover:underline flex items-center gap-1 cursor-pointer"
                      @click="showAdvancedSettings = !showAdvancedSettings"
                    >
                      <UIcon :name="showAdvancedSettings ? 'i-lucide-chevron-up' : 'i-lucide-chevron-down'" class="size-3.5" />
                      {{ showAdvancedSettings ? 'Hide Tolerances' : 'Adjust Tolerances' }}
                    </button>
                  </div>

                  <div class="flex items-center gap-3">
                    <UButton
                      color="primary"
                      size="md"
                      icon="i-lucide-play"
                      :loading="isSubmitting"
                      :disabled="selectedHistoricalRunIds.length < 2"
                      :label="`Verify ${selectedHistoricalRunIds.length > 0 ? selectedHistoricalRunIds.length : ''} Selected Historical Runs`"
                      @click="submitRunsVerification(selectedHistoricalRunIds)"
                    />
                  </div>
                </div>

                <!-- Advanced Tolerances Drawer (Historical Runs) -->
                <div v-if="showAdvancedSettings" class="p-4 bg-neutral-50 rounded-lg grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs">
                  <div>
                    <label class="block font-medium mb-1">Relative Tol (rtol)</label>
                    <UInput v-model.number="toleranceSettings.rel_tol" type="number" step="0.0001" size="xs" />
                  </div>
                  <div>
                    <label class="block font-medium mb-1">Min Absolute Tol (atol_min)</label>
                    <UInput v-model.number="toleranceSettings.abs_tol_min" type="number" step="0.0001" size="xs" />
                  </div>
                  <div>
                    <label class="block font-medium mb-1">Absolute Tol Scale (atol_scale)</label>
                    <UInput v-model.number="toleranceSettings.abs_tol_scale" type="number" step="0.00001" size="xs" />
                  </div>
                </div>
              </div>
            </div>
          </UCard>
        </Transition>
        <!-- END ISLAND 3: Step 3 Card -->
      </div>

      <!-- Tab 2: Lookup Past Verifications -->
      <div v-if="activeMode === 'lookup'" class="space-y-6">
        <!-- Direct Run or Archive Lookup Card -->
        <UCard class="shadow-sm border border-neutral-200">
          <template #header>
            <div class="flex items-start gap-4">
              <div class="size-10 rounded-xl bg-primary/10 flex items-center justify-center text-primary shrink-0">
                <UIcon :name="isLookupHash ? 'i-lucide-archive' : 'i-lucide-search'" class="size-5" />
              </div>
              <div>
                <h2 class="text-base sm:text-lg font-semibold text-neutral-900 flex items-center gap-2">
                  Direct Run or Archive Lookup
                </h2>
                <p class="text-xs text-neutral-500 mt-1 max-w-2xl leading-relaxed">
                  Directly inspect any verification run using its Run ID, or filter prior runs across the platform by OMEX archive hash.
                </p>
              </div>
            </div>
          </template>

          <div class="space-y-2">
            <label class="block text-xs font-semibold uppercase tracking-wider text-neutral-600">
              Verification Run ID or OMEX Hash
            </label>
            <div class="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
              <div class="flex-1">
                <UInput
                  v-model="lookupIdInput"
                  placeholder="e.g. omex-verification-b2ac553f... or OMEX archive hash..."
                  size="md"
                  :icon="isLookupHash ? 'i-lucide-archive' : 'i-lucide-hash'"
                  class="w-full font-mono text-xs"
                  @keydown.enter="handleDirectLookup"
                />
              </div>
              <div class="flex items-center gap-2">
                <UButton
                  v-if="lookupIdInput"
                  variant="ghost"
                  color="neutral"
                  size="md"
                  icon="i-lucide-x"
                  label="Clear"
                  @click="lookupIdInput = ''"
                />
                <UButton
                  color="primary"
                  size="md"
                  :icon="isLookupHash ? 'i-lucide-archive' : 'i-lucide-arrow-right'"
                  :label="isLookupHash ? 'Filter Archive' : 'Load Report'"
                  :disabled="!lookupIdInput.trim()"
                  @click="handleDirectLookup"
                />
              </div>
            </div>
          </div>
        </UCard>

        <!-- Prior Verification Runs Catalog Card -->
        <UCard ref="catalogCardRef" class="shadow-sm border border-neutral-200">
          <template #header>
            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
              <div class="flex items-start gap-3">
                <div class="size-10 rounded-xl bg-primary/10 flex items-center justify-center text-primary shrink-0">
                  <UIcon name="i-lucide-database" class="size-5" />
                </div>
                <div>
                  <div class="flex items-center gap-2">
                    <h2 class="text-base sm:text-lg font-semibold text-neutral-900">
                      Prior Verification Runs
                    </h2>
                    <UBadge
                      v-if="pastVerifications.length > 0"
                      color="neutral"
                      variant="subtle"
                      size="md"
                      class="text-xs font-medium px-2 py-0.5"
                    >
                      {{ pastVerifications.length }} loaded
                    </UBadge>
                  </div>
                  <p class="text-xs text-neutral-500 mt-1">
                    Browse and inspect previous verification runs recorded on the platform.
                  </p>
                </div>
              </div>

              <div class="flex items-center gap-2">
                <UButton
                  variant="outline"
                  color="neutral"
                  size="xs"
                  icon="i-lucide-refresh-cw"
                  :loading="isLoadingPastIds"
                  label="Refresh"
                  @click="fetchPastVerifications(true)"
                />
              </div>
            </div>
          </template>

          <div class="space-y-4">
            <!-- Filter & Search Toolbar -->
            <div class="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 pb-3 border-b border-neutral-100">
              <div class="flex-1 max-w-md flex items-center gap-1.5">
                <UInput
                  v-model="pastSearchQuery"
                  placeholder="Filter runs by run ID or OMEX hash..."
                  size="sm"
                  icon="i-lucide-search"
                  class="flex-1 font-mono text-xs"
                  @keydown.enter="handleCatalogSearchEnter"
                />
                <UButton
                  v-if="pastSearchQuery"
                  variant="ghost"
                  color="neutral"
                  size="xs"
                  icon="i-lucide-x"
                  title="Clear filter"
                  @click="pastSearchQuery = ''"
                />
                <UButton
                  v-if="isPastSearchQueryHash"
                  variant="subtle"
                  color="primary"
                  size="xs"
                  icon="i-lucide-archive"
                  title="Filter all runs by this OMEX hash"
                  label="Filter Archive"
                  @click="applyHashFilterFromSearch"
                />
              </div>

              <!-- Type Filter Buttons -->
              <div class="flex items-center gap-1.5 self-start sm:self-auto">
                <span class="text-xs text-neutral-400 mr-1 hidden sm:inline">Type:</span>
                <UButton
                  size="xs"
                  :variant="pastTypeFilter === 'all' ? 'solid' : 'ghost'"
                  :color="pastTypeFilter === 'all' ? 'primary' : 'neutral'"
                  label="All"
                  @click="pastTypeFilter = 'all'"
                />
                <UButton
                  size="xs"
                  :variant="pastTypeFilter === 'omex' ? 'solid' : 'ghost'"
                  :color="pastTypeFilter === 'omex' ? 'primary' : 'neutral'"
                  label="OMEX Archive"
                  icon="i-lucide-archive"
                  @click="pastTypeFilter = 'omex'"
                />
                <UButton
                  size="xs"
                  :variant="pastTypeFilter === 'runs' ? 'solid' : 'ghost'"
                  :color="pastTypeFilter === 'runs' ? 'secondary' : 'neutral'"
                  label="Multi-Run"
                  icon="i-lucide-layers"
                  @click="pastTypeFilter = 'runs'"
                />
              </div>
            </div>

            <!-- Archive filter, applied by the server -->
            <div v-if="pastHashFilter" class="flex flex-wrap items-center gap-2 text-xs text-neutral-600">
              <UIcon name="i-lucide-archive" class="size-4 text-primary shrink-0" />
              <span>Showing verifications of archive</span>
              <code class="font-mono text-xs bg-neutral-100 dark:bg-neutral-800 px-2 py-0.5 rounded font-medium text-neutral-800 dark:text-neutral-200 select-all" :title="pastHashFilter">
                {{ pastHashFilter }}
              </code>
              <UButton
                size="xs"
                variant="ghost"
                color="neutral"
                icon="i-lucide-x"
                label="Show all"
                @click="clearHashFilter"
              />
            </div>

            <!-- Error Banner -->
            <UAlert
              v-if="pastIdsError"
              color="error"
              variant="subtle"
              icon="i-lucide-alert-circle"
              title="Failed to Load Past Verifications"
              :description="pastIdsError"
            >
              <template #actions>
                <UButton
                  size="xs"
                  color="error"
                  variant="outline"
                  label="Retry"
                  @click="fetchPastVerifications(true)"
                />
              </template>
            </UAlert>

            <!-- Loading Skeleton -->
            <div v-if="isLoadingPastIds" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 py-2">
              <div
                v-for="i in 6"
                :key="i"
                class="p-4 rounded-xl border border-neutral-200 bg-neutral-50/50 animate-pulse space-y-3"
              >
                <div class="flex items-center justify-between">
                  <div class="h-5 bg-neutral-200 rounded w-28" />
                  <div class="size-6 bg-neutral-200 rounded" />
                </div>
                <div class="space-y-1.5 py-1">
                  <div class="h-3 bg-neutral-200 rounded w-16" />
                  <div class="h-4 bg-neutral-200 rounded w-full" />
                </div>
                <div class="pt-3 border-t border-neutral-100 flex justify-between items-center">
                  <div class="h-3 bg-neutral-200 rounded w-20" />
                  <div class="h-6 bg-neutral-200 rounded w-20" />
                </div>
              </div>
            </div>

            <!-- Empty State (No items at all) -->
            <div
              v-else-if="pastVerifications.length === 0"
              class="py-12 px-4 text-center border-2 border-dashed border-neutral-200 rounded-xl"
            >
              <div class="size-12 rounded-full bg-neutral-100 text-neutral-400 flex items-center justify-center mx-auto mb-3">
                <UIcon name="i-lucide-inbox" class="size-6" />
              </div>
              <h3 class="text-sm font-semibold text-neutral-900">
                <template v-if="!pastHashFilter">No Past Verifications Found</template>
                <template v-else-if="pastListingSummaries">No Verifications of This Archive</template>
                <template v-else>Archive Lookup Unavailable</template>
              </h3>
              <p v-if="!pastHashFilter" class="text-xs text-neutral-500 mt-1 max-w-sm mx-auto">
                No past verification runs were found. Run a new verification in the "Verify Biomodel Archive" tab to start generating comparison history.
              </p>
              <p v-else-if="pastListingSummaries" class="text-xs text-neutral-500 mt-1 max-w-sm mx-auto">
                No verification runs are recorded for this archive yet.
              </p>
              <!-- An older server ignores omex_hash, which says nothing about this archive's history -->
              <p v-else class="text-xs text-neutral-500 mt-1 max-w-sm mx-auto">
                Archive lookup is unavailable. Choose "Show all" to browse all verifications.
              </p>
              <UButton
                size="sm"
                color="primary"
                variant="subtle"
                icon="i-lucide-play"
                label="Run New Verification"
                class="mt-4"
                @click="activeMode = 'verify'"
              />
            </div>

            <!-- Empty State (Filtered out) -->
            <div
              v-else-if="filteredPastVerifications.length === 0"
              class="py-10 px-4 text-center border border-neutral-100 rounded-xl bg-neutral-50/50"
            >
              <div class="size-10 rounded-full bg-neutral-100 text-neutral-400 flex items-center justify-center mx-auto mb-2">
                <UIcon name="i-lucide-search-x" class="size-5" />
              </div>
              <h3 class="text-sm font-semibold text-neutral-900">No Matching Verifications</h3>
              <p class="text-xs text-neutral-500 mt-1">
                No loaded runs match the filter "{{ pastSearchQuery }}" or selected type.
              </p>
              <div class="mt-3 flex flex-wrap items-center justify-center gap-2">
                <UButton
                  v-if="isPastSearchQueryHash"
                  size="xs"
                  color="primary"
                  variant="solid"
                  icon="i-lucide-archive"
                  label="Search Runs for This Archive Hash"
                  @click="applyHashFilterFromSearch"
                />
                <UButton
                  size="xs"
                  color="neutral"
                  variant="outline"
                  label="Reset Filters"
                  @click="pastSearchQuery = ''; pastTypeFilter = 'all'"
                />
              </div>
            </div>

            <!-- Verification Grid of Cards -->
            <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              <div
                v-for="item in filteredPastVerifications"
                :key="item.workflow_id"
                class="relative flex flex-col justify-between p-4 rounded-xl border border-neutral-200 transition-all duration-200 cursor-pointer group bg-white shadow-xs hover:shadow-md hover:border-primary-400"
                @click="openWorkflowReport(item.workflow_id)"
              >
                <div class="space-y-3">
                  <!-- Header Row: Run ID + Action Buttons -->
                  <div class="flex items-start justify-between gap-2">
                    <div class="min-w-0">
                      <span class="text-xs font-semibold text-neutral-400 uppercase tracking-wider block">
                        Run ID
                      </span>
                      <div class="font-mono text-xs font-semibold text-neutral-800 break-all group-hover:text-primary transition-colors leading-relaxed select-all mt-0.5">
                        {{ item.workflow_id }}
                      </div>
                    </div>

                    <div class="flex items-center gap-0.5 shrink-0 pt-0.5">
                      <UButton
                        size="xs"
                        color="neutral"
                        variant="ghost"
                        icon="i-lucide-link"
                        class="opacity-60 group-hover:opacity-100 transition-opacity"
                        title="Copy Permalink"
                        @click.stop="copyPermalink(item.workflow_id)"
                      />
                      <UButton
                        size="xs"
                        color="neutral"
                        variant="ghost"
                        icon="i-lucide-copy"
                        class="opacity-60 group-hover:opacity-100 transition-opacity"
                        title="Copy Run ID"
                        @click.stop="copyWorkflowId(item.workflow_id)"
                      />
                    </div>
                  </div>

                  <!-- Untruncated OMEX Hash (Immediately Underneath Header Row) -->
                  <div v-if="item.omex_hash" class="pt-0.5">
                    <UBadge
                      color="neutral"
                      variant="subtle"
                      size="md"
                      class="font-mono text-xs text-neutral-700 dark:text-neutral-300 break-all cursor-pointer hover:bg-neutral-200 dark:hover:bg-neutral-800 transition-colors w-full justify-start items-center gap-1.5 py-1 px-2.5 font-medium"
                      :title="`Click to filter runs by OMEX Hash: ${item.omex_hash}`"
                      @click.stop="filterByCardHash(item.omex_hash)"
                    >
                      <UIcon name="i-lucide-archive" class="size-3.5 shrink-0 text-neutral-400" />
                      <span class="text-[10px] uppercase font-sans font-semibold tracking-wider text-neutral-400 shrink-0">OMEX Hash</span>
                      <span class="break-all select-all font-mono">{{ item.omex_hash }}</span>
                    </UBadge>
                  </div>

                  <!-- Type & Status (Enlarged for Legibility) -->
                  <div class="flex flex-wrap items-center gap-2 pt-0.5">
                    <UBadge
                      :color="getVerificationType(verificationTypeOf(item)).color"
                      variant="subtle"
                      size="md"
                      class="font-medium text-xs py-1 px-2.5"
                    >
                      <UIcon :name="getVerificationType(verificationTypeOf(item)).icon" class="size-3.5 mr-1" />
                      {{ getVerificationType(verificationTypeOf(item)).label }}
                    </UBadge>
                    <UBadge
                      v-if="item.status !== undefined"
                      :color="getStatusBadge(item.status).color"
                      variant="subtle"
                      size="md"
                      class="font-medium text-xs py-1 px-2.5"
                      :title="item.status ? undefined : 'No finished status recorded yet. Open the report to check.'"
                    >
                      {{ getStatusBadge(item.status).label }}
                    </UBadge>
                  </div>
                </div>

                <!-- Footer: Created + View Report Button -->
                <div class="pt-3 mt-4 border-t border-neutral-100 flex items-center justify-between gap-2 text-xs">
                  <span v-if="formatCreated(item.created)" class="text-[11px] text-neutral-400" :title="item.created ?? undefined">
                    {{ formatCreated(item.created) }}
                  </span>
                  <UButton
                    size="xs"
                    color="primary"
                    variant="soft"
                    icon="i-lucide-arrow-up-right"
                    label="View Report"
                    class="ml-auto group-hover:bg-primary group-hover:text-white transition-colors"
                    @click.stop="openWorkflowReport(item.workflow_id)"
                  />
                </div>
              </div>
            </div>

            <!-- Pagination / Continuation Controls -->
            <div
              v-if="pastNextCursor || pastVerifications.length > 0"
              class="pt-3 flex flex-col sm:flex-row items-center justify-between gap-3 text-xs text-neutral-500"
            >
              <span>
                Showing {{ filteredPastVerifications.length }} of {{ pastVerifications.length }} runs
              </span>

              <UButton
                v-if="pastNextCursor"
                color="neutral"
                variant="outline"
                size="sm"
                icon="i-lucide-chevron-down"
                :loading="isLoadingMorePastIds"
                label="Load More Verifications"
                @click="loadMorePastVerifications"
              />
            </div>
          </div>
        </UCard>
      </div>

      <!-- Global Submission Error Banner -->
      <UAlert
        v-if="submissionError"
        color="error"
        variant="subtle"
        icon="i-lucide-alert-circle"
        title="Submission Error"
        :description="submissionError"
        close
        @update:open="submissionError = null"
      />
    </div>

    <!-- Historical Verification Run Modal (Tab 2) -->
    <UModal
      v-model:open="isHistoricalModalOpen"
      :fullscreen="isHistoricalModalFullscreen"
      data-lenis-prevent
      :ui="{
        content: isHistoricalModalFullscreen
          ? 'w-screen h-dvh max-w-none max-h-none rounded-none flex flex-col lenis-prevent'
          : 'sm:max-w-6xl max-w-7xl w-full max-h-[92vh] flex flex-col lenis-prevent',
        body: 'overflow-y-auto min-h-0 overscroll-contain p-4 sm:p-6 space-y-6 flex-1 lenis-prevent'
      }"
    >
      <template #header>
        <div class="flex items-center justify-between w-full">
          <div class="flex items-center gap-3 min-w-0">
            <div class="size-9 rounded-lg bg-primary/10 flex items-center justify-center text-primary shrink-0">
              <UIcon :name="getVerificationType(activeVerifyType).icon" class="size-5" />
            </div>
            <div class="min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <h3 class="text-base font-semibold text-neutral-900 truncate">
                  Verification Report
                </h3>
                <UBadge
                  :color="getVerificationType(activeVerifyType).color"
                  variant="subtle"
                  size="xs"
                >
                  {{ getVerificationType(activeVerifyType).label }}
                </UBadge>
              </div>
              <div class="font-mono text-xs text-neutral-500 flex items-center gap-1.5 mt-0.5 truncate select-all">
                <span class="truncate">{{ activeWorkflowId }}</span>
                <UButton
                  size="xs"
                  variant="ghost"
                  color="neutral"
                  icon="i-lucide-link"
                  class="p-0.5"
                  title="Copy Permalink"
                  @click="copyPermalink(activeWorkflowId)"
                />
                <UButton
                  size="xs"
                  variant="ghost"
                  color="neutral"
                  icon="i-lucide-copy"
                  class="p-0.5"
                  title="Copy Workflow ID"
                  @click="copyWorkflowId(activeWorkflowId)"
                />
              </div>
            </div>
          </div>
          <div class="flex items-center gap-1 shrink-0">
            <UButton
              size="sm"
              variant="ghost"
              color="neutral"
              :icon="isHistoricalModalFullscreen ? 'i-lucide-minimize-2' : 'i-lucide-maximize-2'"
              :title="isHistoricalModalFullscreen ? 'Exit Fullscreen' : 'Enter Fullscreen'"
              @click="isHistoricalModalFullscreen = !isHistoricalModalFullscreen"
            />
            <UButton
              size="sm"
              variant="ghost"
              color="neutral"
              icon="i-lucide-x"
              title="Close Report"
              @click="isHistoricalModalOpen = false"
            />
          </div>
        </div>
      </template>

      <template #body>
        <div class="space-y-6">
          <!-- Live Polling Status Component -->
          <VerificationStatus
            v-if="activeWorkflowId"
            :model-value="workflowOutput"
            :workflow-id="activeWorkflowId"
            :auto-poll="true"
            @update:model-value="onWorkflowOutput"
          />

          <!-- Results Display when COMPLETED -->
          <div v-if="workflowOutput?.workflow_status === 'COMPLETED'" class="space-y-6">
            <!-- Dataset Selector & KPI Cards -->
            <div class="flex flex-col items-stretch justify-between gap-4">
              <!-- Dataset Selector -->
              <div class="flex items-center gap-2">
                <span class="text-xs font-semibold whitespace-nowrap text-neutral-500 uppercase tracking-wider">Dataset / Report:</span>
                <div class="flex-1">
                  <USelect
                    v-model="activeDataset"
                    :items="availableDatasets"
                    size="sm"
                    class="font-mono text-xs w-full"
                  />
                </div>
              </div>

              <!-- KPI Cards -->
              <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                <UCard variant="subtle" class="text-center shadow-xs" :ui="{ body: 'p-3' }">
                  <span class="text-neutral-400 block text-[10px] uppercase font-semibold">Overall Concordance</span>
                  <span class="text-lg font-bold font-mono" :class="concordanceClass">
                    {{ concordanceSummary.percentage === null ? '—' : `${concordanceSummary.percentage}%` }}
                  </span>
                  <span
                    v-if="concordanceSummary.comparedPairs < concordanceSummary.totalPairs"
                    class="block text-[10px] text-amber-600"
                  >
                    {{ concordanceSummary.comparedPairs }} of {{ concordanceSummary.totalPairs }} pairs compared
                  </span>
                </UCard>

                <UCard variant="subtle" class="text-center shadow-xs" :ui="{ body: 'p-3' }">
                  <span class="text-neutral-400 block text-[10px] uppercase font-semibold">Solvers Compared</span>
                  <span class="text-lg font-bold font-mono text-neutral-800">
                    {{ simulatorsList.length }}
                    <span v-if="excludedSimulatorsList.length > 0" class="text-xs font-normal text-amber-600">
                      ({{ excludedSimulatorsList.length }} failed)
                    </span>
                  </span>
                </UCard>

                <UCard variant="subtle" class="text-center shadow-xs" :ui="{ body: 'p-3' }">
                  <span class="text-neutral-400 block text-[10px] uppercase font-semibold">Variables</span>
                  <span class="text-lg font-bold font-mono text-neutral-800">
                    {{ variableComparisonRows.length }}
                  </span>
                </UCard>

                <UCard variant="subtle" class="text-center shadow-xs" :ui="{ body: 'p-3' }">
                  <span class="text-neutral-400 block text-[10px] uppercase font-semibold">Outliers Flagged</span>
                  <span
                    class="text-lg font-bold font-mono"
                    :class="concordanceSummary.percentage === null ? 'text-neutral-400' : totalOutlierCount > 0 ? 'text-amber-600 font-bold' : 'text-emerald-600'"
                  >
                    {{ totalOutlierCount }}
                  </span>
                </UCard>
              </div>
            </div>

            <UAlert
              v-if="concordanceNotice"
              color="warning"
              variant="subtle"
              icon="i-lucide-alert-triangle"
              title="Nothing to compare"
              :description="concordanceNotice"
            />

            <!-- Component 1: Concordance Heatmap Matrix -->
            <VerificationHeatmap
              :simulators="simulatorsList"
              :excluded-simulators="excludedSimulatorsList"
              :matrix="currentMatrix"
              :selected-i="selectedI"
              :selected-j="selectedJ"
              @select-pair="onSelectHeatmapPair"
            />

            <!-- Component 2: Concordance & Outlier Table -->
            <VerificationTable
              :rows="variableComparisonRows"
              :pair-label="selectedPairLabel"
              :has-outputs="Boolean(workflowOutput?.workflow_results?.sim_run_data)"
              @visualize="onVisualizeVariable"
            />

            <!-- Component 3: Plotly Multi-Solution Viewer -->
            <div ref="modalPlotSectionRef">
              <VerificationPlotViewer
                :dataset-name="activeDataset"
                :sim-run-data="workflowOutput?.workflow_results?.sim_run_data"
                :sims-run-info="workflowOutput?.workflow_results?.sims_run_info"
                :initial-variable="activeVariable"
                :initial-sim-i="selectedI"
                :initial-sim-j="selectedJ"
              />
            </div>
          </div>
        </div>
      </template>

      <template #footer>
        <div class="flex items-center justify-between w-full">
          <div class="text-xs text-neutral-400 font-mono truncate max-w-sm">
            Run ID: {{ activeWorkflowId }}
          </div>
          <UButton
            color="neutral"
            variant="outline"
            size="sm"
            label="Close Report"
            @click="isHistoricalModalOpen = false"
          />
        </div>
      </template>
    </UModal>

    <!-- Platform Run Picker Modal -->
    <PlatformRunPickerModal
      v-model:open="isRunPickerModalOpen"
      @select="onPlatformRunSelected"
    />
  </div>
</template>

<style scoped>
.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}

.fade-slide-enter-from,
.fade-slide-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}
</style>
