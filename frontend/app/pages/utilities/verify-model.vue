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
  VerificationRecord,
  VerifyWorkflowOutput
} from '~/models/verification'
import { useVerificationAnalytics } from '~/composables/useVerificationAnalytics'
import type { ExcludedSimulatorInfo, SimulatorInfo } from '~/components/verification/VerificationHeatmap.vue'
import PlatformRunPickerModal from '~/components/verification/PlatformRunPickerModal.vue'

const config = useRuntimeConfig()
const route = useRoute()
const router = useRouter()
const toast = useToast()
const { copy } = useClipboard()
const { buildVariableComparisonRows, precheckRunIds } = useVerificationAnalytics()

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

function clearFile() {
  omexFile.value = null
  compatibilityResponse.value = null
  compatibilityError.value = null
  compatibleSimulators.value = []
  historicalGroups.value = {}
  historicalRunsError.value = null
  step2Mode.value = 'solvers'
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
  compatibilityResponse.value = null
  compatibilityError.value = null
  compatibleSimulators.value = []
  historicalGroups.value = {}
  historicalRunsError.value = null
  step2Mode.value = 'solvers'
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
  compatibilityResponse.value = null
  compatibilityError.value = null
  compatibleSimulators.value = []
  historicalGroups.value = {}
  historicalRunsError.value = null
  step2Mode.value = 'solvers'
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

    // Query past platform verification records to detect previous workflows for this archive
    await fetchPastVerificationIds(true)
  } catch (err: any) {
    compatibilityError.value = err?.data?.detail || err?.message || 'Could not verify archive compatibility.'
    compatibleSimulators.value = []
  } finally {
    checkingCompatibility.value = false
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
const pastVerificationsForHash = computed<string[]>(() => {
  const hash = compatibilityResponse.value?.omex_id?.toLowerCase()
  if (!hash || pastVerificationRecords.value.length === 0) return []

  // Check server-provided verification records for this archive hash
  const matchingIds: string[] = []
  for (const record of pastVerificationRecords.value) {
    if (record.omex_hash && record.omex_hash.toLowerCase() === hash) {
      if (Array.isArray(record.run_ids)) {
        matchingIds.push(...record.run_ids)
      }
    }
  }

  return [...new Set(matchingIds)]
})

function viewPastReportsForCurrentHash() {
  const hash = compatibilityResponse.value?.omex_id
  if (!hash) return
  pastSearchQuery.value = hash
  pastTypeFilter.value = 'all'
  activeMode.value = 'lookup'
  router.push({
    query: {
      ...route.query,
      tab: 'lookup',
      omex_hash: hash
    }
  })
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
          throw new Error(`Failed to download archive from URL: HTTP ${resp.status}`)
        }
        fileToSend = await resp.blob()
      } catch (dlErr: any) {
        throw new Error(`Could not retrieve OMEX archive file from ${urlToUse}: ${dlErr.message}`)
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
    if (compatibilityResponse.value?.omex_id) {
      queryParams.set('omex_hash', compatibilityResponse.value.omex_id)
    }

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
      activeWorkflowId.value = res.workflow_id
      workflowOutput.value = res

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

      if (compatibilityResponse.value?.omex_id) {
        const currentHash = compatibilityResponse.value.omex_id
        const existingRecord = pastVerificationRecords.value.find(r => r.omex_hash === currentHash)
        if (existingRecord) {
          if (!existingRecord.run_ids.includes(res.workflow_id)) {
            existingRecord.run_ids.unshift(res.workflow_id)
          }
        } else {
          pastVerificationRecords.value.unshift({
            omex_hash: currentHash,
            run_ids: [res.workflow_id]
          })
        }
      }

      // Slide user over to Past Verifications tab and open newly created record in modal
      activeMode.value = 'lookup'
      isHistoricalModalOpen.value = true
      fetchPastVerificationIds(true)
      router.push({ query: { ...route.query, tab: 'lookup', workflow_id: res.workflow_id } })
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
    precheckResult.value = result
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
      activeWorkflowId.value = res.workflow_id
      workflowOutput.value = res
      if (precheckResult.value) {
        try {
          sessionStorage.setItem(`verify_precheck_${res.workflow_id}`, JSON.stringify(precheckResult.value))
        } catch {
          // Ignore sessionStorage write errors
        }
      }
      if (compatibilityResponse.value?.omex_id) {
        const currentHash = compatibilityResponse.value.omex_id
        const existingRecord = pastVerificationRecords.value.find(r => r.omex_hash === currentHash)
        if (existingRecord) {
          if (!existingRecord.run_ids.includes(res.workflow_id)) {
            existingRecord.run_ids.unshift(res.workflow_id)
          }
        } else {
          pastVerificationRecords.value.unshift({
            omex_hash: currentHash,
            run_ids: [res.workflow_id]
          })
        }
      }

      // Slide user over to Past Verifications tab and open newly created record in modal
      activeMode.value = 'lookup'
      isHistoricalModalOpen.value = true
      fetchPastVerificationIds(true)
      router.push({ query: { ...route.query, tab: 'lookup', workflow_id: res.workflow_id } })
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
const dashboardRef = ref<HTMLElement | null>(null)
const pastVerificationRecords = ref<VerificationRecord[]>([])
const pastVerificationIds = ref<string[]>([])
const pastNextCursor = ref<string | null>(null)
const isLoadingPastIds = ref<boolean>(false)
const isLoadingMorePastIds = ref<boolean>(false)
const pastIdsError = ref<string | null>(null)
const pastSearchQuery = ref<string>('')
const pastTypeFilter = ref<'all' | 'omex' | 'runs'>('all')

const hashByWorkflowId = computed<Record<string, string>>(() => {
  const map: Record<string, string> = {}
  for (const record of pastVerificationRecords.value) {
    if (record.omex_hash && Array.isArray(record.run_ids)) {
      for (const id of record.run_ids) {
        map[id] = record.omex_hash
      }
    }
  }
  return map
})

const filteredPastVerificationIds = computed(() => {
  const query = pastSearchQuery.value.trim().toLowerCase()
  return pastVerificationIds.value.filter((id) => {
    if (pastTypeFilter.value === 'omex' && !id.startsWith('omex-')) return false
    if (pastTypeFilter.value === 'runs' && !id.startsWith('runs-')) return false
    if (query.length > 0) {
      const matchesId = id.toLowerCase().includes(query)
      const associatedHash = hashByWorkflowId.value[id]?.toLowerCase() || ''
      const matchesHash = associatedHash.includes(query)
      return matchesId || matchesHash
    }
    return true
  })
})

function getVerificationType(id?: string | null): { label: string; icon: string; color: 'primary' | 'secondary' | 'neutral' } {
  if (!id) {
    return { label: 'Workflow', icon: 'i-lucide-hash', color: 'neutral' }
  }
  if (id.startsWith('omex-')) {
    return { label: 'OMEX Archive', icon: 'i-lucide-archive', color: 'primary' }
  }
  if (id.startsWith('runs-')) {
    return { label: 'Multi-Run', icon: 'i-lucide-layers', color: 'secondary' }
  }
  return { label: 'Workflow', icon: 'i-lucide-hash', color: 'neutral' }
}

function copyWorkflowId(id?: string | null) {
  if (!id) return
  copy(id)
  toast.add({
    title: 'ID Copied',
    description: `Workflow ID ${id} copied to clipboard.`,
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

async function fetchPastVerificationIds(reset = true) {
  if (reset) {
    isLoadingPastIds.value = true
    pastIdsError.value = null
  } else {
    isLoadingMorePastIds.value = true
  }

  try {
    const params: Record<string, any> = {
      limit: 50
    }
    if (!reset && pastNextCursor.value) {
      params.cursor = pastNextCursor.value
    }

    const res = await $fetch<VerificationIdsResponse | VerificationRecord[] | string[]>(`${config.public.api_url}/verification_ids`, {
      params
    })

    let incomingRecords: VerificationRecord[] = []
    let nextCursor: string | null = null

    if (Array.isArray(res)) {
      if (res.length > 0 && typeof res[0] === 'object' && res[0] !== null && 'omex_hash' in res[0]) {
        incomingRecords = res as VerificationRecord[]
      } else if (res.length > 0 && typeof res[0] === 'string') {
        incomingRecords = [{ omex_hash: '', run_ids: res as string[] }]
      }
    } else if (res && typeof res === 'object') {
      nextCursor = res.next_cursor || null
      if (Array.isArray(res.records)) {
        incomingRecords = res.records
      } else if (Array.isArray(res.verification_records)) {
        incomingRecords = res.verification_records
      } else if (Array.isArray(res.verification_ids)) {
        incomingRecords = [{ omex_hash: '', run_ids: res.verification_ids }]
      }
    }

    if (reset) {
      pastVerificationRecords.value = incomingRecords
      const allIds: string[] = []
      const seen = new Set<string>()
      for (const rec of incomingRecords) {
        for (const id of rec.run_ids || []) {
          if (!seen.has(id)) {
            seen.add(id)
            allIds.push(id)
          }
        }
      }
      pastVerificationIds.value = allIds
    } else {
      pastVerificationRecords.value.push(...incomingRecords)
      const existing = new Set(pastVerificationIds.value)
      for (const rec of incomingRecords) {
        for (const id of rec.run_ids || []) {
          if (!existing.has(id)) {
            existing.add(id)
            pastVerificationIds.value.push(id)
          }
        }
      }
    }
    pastNextCursor.value = nextCursor
  } catch (err: any) {
    const msg = err?.data?.detail || err?.message || 'Failed to load past verification IDs from server.'
    pastIdsError.value = msg
  } finally {
    isLoadingPastIds.value = false
    isLoadingMorePastIds.value = false
  }
}

function loadMorePastVerificationIds() {
  if (!pastNextCursor.value || isLoadingMorePastIds.value) return
  fetchPastVerificationIds(false)
}

const isHistoricalModalOpen = ref(false)
const isHistoricalModalFullscreen = ref(false)

function openHistoricalModal(id: string) {
  if (!id.trim()) return
  const wId = id.trim()
  activeWorkflowId.value = wId
  lookupIdInput.value = wId
  activeMode.value = 'lookup'
  isHistoricalModalOpen.value = true

  try {
    const saved = sessionStorage.getItem(`verify_precheck_${wId}`)
    if (saved) {
      precheckResult.value = JSON.parse(saved)
    }
  } catch {
    // Ignore sessionStorage read errors
  }

  router.push({ query: { ...route.query, tab: 'lookup', workflow_id: wId } })
}

function loadWorkflowById(id: string) {
  if (!id.trim()) return
  const wId = id.trim()
  activeWorkflowId.value = wId
  lookupIdInput.value = wId
  try {
    const saved = sessionStorage.getItem(`verify_precheck_${wId}`)
    if (saved) {
      precheckResult.value = JSON.parse(saved)
    }
  } catch {
    // Ignore sessionStorage read errors
  }
  activeMode.value = 'lookup'
  isHistoricalModalOpen.value = true
  router.push({ query: { ...route.query, tab: 'lookup', workflow_id: wId } })
}

watch(activeMode, (mode) => {
  if (mode === 'lookup') {
    if (pastVerificationIds.value.length === 0 && !isLoadingPastIds.value) {
      fetchPastVerificationIds(true)
    }
  } else {
    isHistoricalModalOpen.value = false
    isHistoricalModalFullscreen.value = false
  }

  // Synchronize activeMode with the tab URL query param
  const currentTab = route.query.tab
  const targetTab = mode === 'lookup' ? 'lookup' : undefined
  if (currentTab !== targetTab) {
    const q = { ...route.query }
    if (targetTab) {
      q.tab = targetTab
    } else {
      delete q.tab
    }
    router.replace({ query: q })
  }
})

watch(isHistoricalModalOpen, (isOpen) => {
  if (!isOpen) {
    isHistoricalModalFullscreen.value = false
    if (activeMode.value === 'lookup' && route.query.workflow_id) {
      const q = { ...route.query }
      delete q.workflow_id
      q.tab = 'lookup'
      router.push({ query: q })
    }
  }
})

// Watch route.query.workflow_id so any external or router query updates activate Lookup mode & modal
watch(() => route.query.workflow_id, (newWId) => {
  if (newWId) {
    const wId = String(newWId).trim()
    activeWorkflowId.value = wId
    lookupIdInput.value = wId
    activeMode.value = 'lookup'
    isHistoricalModalOpen.value = true
    if (pastVerificationIds.value.length === 0 && !isLoadingPastIds.value) {
      fetchPastVerificationIds(true)
    }
  }
})

// Watch route.query.omex_hash so hash filter URLs switch to Lookup mode and apply the filter
watch(() => route.query.omex_hash, (newHash) => {
  if (newHash) {
    pastSearchQuery.value = String(newHash).trim()
    activeMode.value = 'lookup'
    if (pastVerificationRecords.value.length === 0 && !isLoadingPastIds.value) {
      fetchPastVerificationIds(true)
    }
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

const overallConcordance = computed(() => {
  const m = currentMatrix.value
  if (!m || m.length === 0) return 0

  let totalPairs = 0
  let concordantPairs = 0

  for (let i = 0; i < m.length; i++) {
    for (let j = i + 1; j < m.length; j++) {
      const stats = m[i]?.[j]
      if (stats && stats.is_close) {
        totalPairs += stats.is_close.length
        concordantPairs += stats.is_close.filter(Boolean).length
      }
    }
  }

  return totalPairs > 0 ? Math.round((concordantPairs / totalPairs) * 100) : 100
})

const totalOutlierCount = computed(() => {
  return variableComparisonRows.value.filter(r => r.outlier_simulators && r.outlier_simulators.length > 0).length
})

function onSelectHeatmapPair(i: number, j: number) {
  selectedI.value = i
  selectedJ.value = j
}

const plotSectionRef = ref<HTMLElement | null>(null)
const modalPlotSectionRef = ref<HTMLElement | null>(null)
function onVisualizeVariable(varName: string) {
  activeVariable.value = varName
  nextTick(() => {
    const target = isHistoricalModalOpen.value ? modalPlotSectionRef.value : plotSectionRef.value
    if (target) {
      target.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  })
}

// On mount: check URL for workflow_id and tab
onMounted(() => {
  if (route.query.omex_hash) {
    pastSearchQuery.value = String(route.query.omex_hash).trim()
    activeMode.value = 'lookup'
  }

  if (route.query.workflow_id) {
    // If a workflow_id is provided in the URL, ALWAYS take the user to the "Lookup" cards and activate the modal
    const wId = String(route.query.workflow_id).trim()
    activeWorkflowId.value = wId
    lookupIdInput.value = wId
    activeMode.value = 'lookup'
    isHistoricalModalOpen.value = true
    try {
      const saved = sessionStorage.getItem(`verify_precheck_${wId}`)
      if (saved) {
        precheckResult.value = JSON.parse(saved)
      }
    } catch {
      // Ignore sessionStorage read errors
    }
    fetchPastVerificationIds(true)
  } else if (route.query.tab === 'lookup' || route.query.omex_hash) {
    activeMode.value = 'lookup'
    fetchPastVerificationIds(true)
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
                Ensure the URL points directly to the raw COMBINE/OMEX or SED-ML archive (CORS or direct public download).
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
                  Hash: {{ compatibilityResponse.omex_id.slice(0, 10) }}…
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
              v-if="pastVerificationsForHash.length > 0"
              color="primary"
              variant="subtle"
              icon="i-lucide-history"
              title="Previous Verification Workflows Found"
              :description="`This OMEX archive has ${pastVerificationsForHash.length} verification workflow(s) recorded on the platform.`"
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
        <!-- Direct ID Query Card -->
        <UCard class="shadow-sm border border-neutral-200">
          <template #header>
            <div class="flex items-start gap-4">
              <div class="size-10 rounded-xl bg-primary/10 flex items-center justify-center text-primary shrink-0">
                <UIcon name="i-lucide-search" class="size-5" />
              </div>
              <div>
                <h2 class="text-base sm:text-lg font-semibold text-neutral-900 flex items-center gap-2">
                  Direct Workflow Lookup
                </h2>
                <p class="text-xs text-neutral-500 mt-1 max-w-2xl leading-relaxed">
                  Directly inspect any verification workflow using its unique Workflow ID from a previous execution or log.
                </p>
              </div>
            </div>
          </template>

          <div class="space-y-2">
            <label class="block text-xs font-semibold uppercase tracking-wider text-neutral-600">
              Verification Workflow ID
            </label>
            <div class="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
              <div class="flex-1">
                <UInput
                  v-model="lookupIdInput"
                  placeholder="e.g. omex-verification-b2ac553f... or runs-verification-..."
                  size="md"
                  icon="i-lucide-hash"
                  class="w-full font-mono text-xs"
                  @keydown.enter="loadWorkflowById(lookupIdInput)"
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
                  icon="i-lucide-arrow-right"
                  label="Load Report"
                  :disabled="!lookupIdInput.trim()"
                  @click="loadWorkflowById(lookupIdInput)"
                />
              </div>
            </div>
          </div>
        </UCard>

        <!-- Prior Verification Runs Catalog Card -->
        <UCard class="shadow-sm border border-neutral-200">
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
                      v-if="pastVerificationIds.length > 0"
                      color="neutral"
                      variant="subtle"
                      size="sm"
                    >
                      {{ pastVerificationIds.length }} loaded
                    </UBadge>
                  </div>
                  <p class="text-xs text-neutral-500 mt-1">
                    Browse and inspect previous verification workflows recorded in the platform database.
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
                  @click="fetchPastVerificationIds(true)"
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
                  placeholder="Filter by ID, prefix, or hash..."
                  size="sm"
                  icon="i-lucide-search"
                  class="flex-1 font-mono text-xs"
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
                  @click="fetchPastVerificationIds(true)"
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
              v-else-if="pastVerificationIds.length === 0"
              class="py-12 px-4 text-center border-2 border-dashed border-neutral-200 rounded-xl"
            >
              <div class="size-12 rounded-full bg-neutral-100 text-neutral-400 flex items-center justify-center mx-auto mb-3">
                <UIcon name="i-lucide-inbox" class="size-6" />
              </div>
              <h3 class="text-sm font-semibold text-neutral-900">No Past Verifications Found</h3>
              <p class="text-xs text-neutral-500 mt-1 max-w-sm mx-auto">
                No verification workflow records were found. Run a new verification in the "Verify Biomodel Archive" tab to start generating comparison history.
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
              v-else-if="filteredPastVerificationIds.length === 0"
              class="py-10 px-4 text-center border border-neutral-100 rounded-xl bg-neutral-50/50"
            >
              <div class="size-10 rounded-full bg-neutral-100 text-neutral-400 flex items-center justify-center mx-auto mb-2">
                <UIcon name="i-lucide-search-x" class="size-5" />
              </div>
              <h3 class="text-sm font-semibold text-neutral-900">No Matching Verifications</h3>
              <p class="text-xs text-neutral-500 mt-1">
                No runs match the filter "{{ pastSearchQuery }}" or selected type.
              </p>
              <UButton
                size="xs"
                color="neutral"
                variant="outline"
                label="Reset Filters"
                class="mt-3"
                @click="pastSearchQuery = ''; pastTypeFilter = 'all'"
              />
            </div>

            <!-- Verification Grid of Cards -->
            <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              <div
                v-for="id in filteredPastVerificationIds"
                :key="id"
                class="relative flex flex-col justify-between p-4 rounded-xl border border-neutral-200 transition-all duration-200 cursor-pointer group bg-white shadow-xs hover:shadow-md hover:border-primary-400"
                @click="openHistoricalModal(id)"
              >
                <div class="space-y-3">
                  <!-- Top Row: Workflow ID Label + Action Buttons -->
                  <div class="flex items-center justify-between gap-2">
                    <div class="flex items-center gap-1.5 min-w-0">
                      <span class="text-[10px] font-semibold text-neutral-400 uppercase tracking-wider">
                        Workflow ID
                      </span>
                      <UBadge
                        v-if="hashByWorkflowId[id]"
                        color="neutral"
                        variant="subtle"
                        size="xs"
                        class="font-mono text-[10px] text-neutral-500 truncate max-w-[140px]"
                        :title="`OMEX Hash: ${hashByWorkflowId[id]}`"
                      >
                        <UIcon name="i-lucide-hash" class="size-2.5 mr-0.5 text-neutral-400" />
                        {{ hashByWorkflowId[id].slice(0, 8) }}…
                      </UBadge>
                    </div>

                    <div class="flex items-center gap-0.5 shrink-0">
                      <UButton
                        size="xs"
                        color="neutral"
                        variant="ghost"
                        icon="i-lucide-link"
                        class="opacity-60 group-hover:opacity-100 transition-opacity"
                        title="Copy Permalink"
                        @click.stop="copyPermalink(id)"
                      />
                      <UButton
                        size="xs"
                        color="neutral"
                        variant="ghost"
                        icon="i-lucide-copy"
                        class="opacity-60 group-hover:opacity-100 transition-opacity"
                        title="Copy Workflow ID"
                        @click.stop="copyWorkflowId(id)"
                      />
                    </div>
                  </div>

                  <!-- Workflow ID Body -->
                  <div>
                    <div class="font-mono text-xs font-semibold text-neutral-800 break-all group-hover:text-primary transition-colors leading-relaxed select-all">
                      {{ id }}
                    </div>
                  </div>
                </div>

                <!-- Footer: View Report Button -->
                <div class="pt-3 mt-4 border-t border-neutral-100 flex items-center justify-end text-xs">
                  <UButton
                    size="xs"
                    color="primary"
                    variant="soft"
                    icon="i-lucide-arrow-up-right"
                    label="View Report"
                    class="group-hover:bg-primary group-hover:text-white transition-colors"
                    @click.stop="openHistoricalModal(id)"
                  />
                </div>
              </div>
            </div>

            <!-- Pagination / Continuation Controls -->
            <div
              v-if="pastNextCursor || (!pastNextCursor && pastVerificationIds.length > 0)"
              class="pt-3 flex flex-col sm:flex-row items-center justify-between gap-3 text-xs text-neutral-500"
            >
              <span>
                Showing {{ filteredPastVerificationIds.length }} of {{ pastVerificationIds.length }} runs
              </span>

              <UButton
                v-if="pastNextCursor"
                color="neutral"
                variant="outline"
                size="sm"
                icon="i-lucide-chevron-down"
                :loading="isLoadingMorePastIds"
                label="Load More Verifications"
                @click="loadMorePastVerificationIds"
              />
              <span v-else class="text-neutral-400 italic">
                All visible verification runs loaded
              </span>
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

      <!-- ---------------------------------------------------- -->
      <!-- Interactive Verification Dashboard Section (New Run) -->
      <!-- ---------------------------------------------------- -->
      <div v-if="activeMode === 'verify' && activeWorkflowId" ref="dashboardRef" class="space-y-6 pt-4">

        <!-- Live Polling Status Component (auto-refreshes every 5s) -->
        <VerificationStatus
          v-model="workflowOutput"
          :workflow-id="activeWorkflowId"
          :auto-poll="true"
        />

        <!-- Results Display when COMPLETED -->
        <div v-if="workflowOutput?.workflow_status === 'COMPLETED'" class="space-y-6">
          <!-- Dataset Selector & KPI Cards -->
          <div class="flex flex-col lg:flex-row items-stretch lg:items-center justify-between gap-4">
            <!-- Dataset Selector -->
            <div class="flex items-center gap-2">
              <span class="text-xs font-semibold text-neutral-500 uppercase tracking-wider">Dataset / Report:</span>
              <div class="w-72">
                <USelect
                  v-model="activeDataset"
                  :items="availableDatasets"
                  size="sm"
                  class="font-mono text-xs"
                />
              </div>
            </div>

            <!-- KPI Cards -->
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
              <UCard variant="subtle" class="text-center shadow-xs" :ui="{ body: 'p-3' }">
                <span class="text-neutral-400 block text-[10px] uppercase font-semibold">Overall Concordance</span>
                <span
                  class="text-lg font-bold font-mono"
                  :class="overallConcordance >= 95 ? 'text-emerald-600' : overallConcordance >= 70 ? 'text-amber-500' : 'text-rose-600'"
                >
                  {{ overallConcordance }}%
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
                  :class="totalOutlierCount > 0 ? 'text-amber-600 font-bold' : 'text-emerald-600'"
                >
                  {{ totalOutlierCount }}
                </span>
              </UCard>
            </div>
          </div>

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
          <div ref="plotSectionRef">
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
              <UIcon :name="getVerificationType(activeWorkflowId).icon" class="size-5" />
            </div>
            <div class="min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <h3 class="text-base font-semibold text-neutral-900 truncate">
                  Verification Report
                </h3>
                <UBadge
                  :color="getVerificationType(activeWorkflowId).color"
                  variant="subtle"
                  size="xs"
                >
                  {{ getVerificationType(activeWorkflowId).label }}
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
            v-model="workflowOutput"
            :workflow-id="activeWorkflowId"
            :auto-poll="true"
          />

          <!-- Results Display when COMPLETED -->
          <div v-if="workflowOutput?.workflow_status === 'COMPLETED'" class="space-y-6">
            <!-- Dataset Selector & KPI Cards -->
            <div class="flex flex-col lg:flex-row items-stretch lg:items-center justify-between gap-4">
              <!-- Dataset Selector -->
              <div class="flex items-center gap-2">
                <span class="text-xs font-semibold text-neutral-500 uppercase tracking-wider">Dataset / Report:</span>
                <div class="w-72">
                  <USelect
                    v-model="activeDataset"
                    :items="availableDatasets"
                    size="sm"
                    class="font-mono text-xs"
                  />
                </div>
              </div>

              <!-- KPI Cards -->
              <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                <UCard variant="subtle" class="text-center shadow-xs" :ui="{ body: 'p-3' }">
                  <span class="text-neutral-400 block text-[10px] uppercase font-semibold">Overall Concordance</span>
                  <span
                    class="text-lg font-bold font-mono"
                    :class="overallConcordance >= 95 ? 'text-emerald-600' : overallConcordance >= 70 ? 'text-amber-500' : 'text-rose-600'"
                  >
                    {{ overallConcordance }}%
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
                    :class="totalOutlierCount > 0 ? 'text-amber-600 font-bold' : 'text-emerald-600'"
                  >
                    {{ totalOutlierCount }}
                  </span>
                </UCard>
              </div>
            </div>

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
            Workflow: {{ activeWorkflowId }}
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
