<script setup lang="ts">
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useClipboard } from '@vueuse/core'
import type { BreadcrumbItem } from '#ui/components/Breadcrumb.vue'
import { useSimulators } from '~/composables/useSimulators'
import type { SimulatorDetail } from '~/models/simulator-view'
import Loading from '~/components/Loading.vue'
import ErrorPage from '~/components/ErrorPage.vue'

const route = useRoute()
const router = useRouter()
const runtimeConfig = useRuntimeConfig()
const toast = useToast()
const { copy } = useClipboard()
const { fetchSimulatorDetail } = useSimulators()

const simulatorId = computed(() => route.params.id as string)
const currentVersionParam = computed(() => {
  return (route.params.version as string) || (route.query.version as string) || 'latest'
})

const loading = ref(true)
const error_encountered = ref<string | undefined>(undefined)
const simulator = ref<SimulatorDetail | undefined>(undefined)
const selectedVersion = ref<string>('')
const breadcrumbs = ref<BreadcrumbItem[]>([])

// Description expand/collapse
const isDescriptionExpanded = ref(false)
const isDescriptionLong = computed(() => {
  return (simulator.value?.description?.length || 0) >= 220
})

// Execution instructions tab
const selectedExecutionTab = ref<'docker' | 'singularity' | 'cli' | 'python'>('docker')

// Validation test filtering
const activeTestFilter = ref<'all' | 'Passed' | 'Warned' | 'Skipped' | 'Failed'>('all')
const testSearchQuery = ref('')
const expandedTestLogs = ref<Record<string, boolean>>({})

// Filtered test results
const filteredTestResults = computed(() => {
  if (!simulator.value?.validationTests?.results) return []
  let list = simulator.value.validationTests.results

  if (activeTestFilter.value !== 'all') {
    list = list.filter(r => r.resultType === activeTestFilter.value)
  }

  if (testSearchQuery.value.trim()) {
    const q = testSearchQuery.value.trim().toLowerCase()
    list = list.filter(r => r.caseId.toLowerCase().includes(q) || r.description.toLowerCase().includes(q))
  }

  return list
})

// Date formatting helper
function formatDate(dateStr?: string | null): string {
  if (!dateStr) return 'N/A'
  try {
    return new Date(dateStr).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    })
  } catch {
    return dateStr
  }
}

// Copy to clipboard with toast notification
function copyText(text: string, label: string) {
  copy(text)
  toast.add({
    title: `${label} copied to clipboard!`,
    color: 'success',
  })
}

// Download raw JSON
function downloadSpecificationsJson() {
  if (!simulator.value) return
  const blob = new Blob([simulator.value.rawJson], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${simulator.value.id}-${simulator.value.version}.json`
  a.click()
  URL.revokeObjectURL(url)
}

// Version change navigation
function onVersionSelect(version: string) {
  if (!version || version === simulator.value?.version) return
  router.push(`/simulators/${simulatorId.value}/${version}`)
}

// Toggle test case log expand
function toggleTestLog(caseId: string) {
  expandedTestLogs.value[caseId] = !expandedTestLogs.value[caseId]
}

// SEO Metadata
useSeoMeta({
  title: () => (simulator.value ? `${simulator.value.name} (v${simulator.value.version}) - Simulator` : 'Simulator Details'),
  description: () => simulator.value?.description || 'Explore simulator details, algorithms, and validation results on BioSimulations.',
  author: () => simulator.value?.authors?.map(a => a.name).join(', ') || 'BioSimulators',
  keywords: () => `biosimulators, simulator, ${simulator.value?.name || ''}, systems biology, computational modeling`,
})

async function loadSimulator() {
  loading.value = true
  error_encountered.value = undefined

  try {
    const data = await fetchSimulatorDetail(simulatorId.value, currentVersionParam.value)
    simulator.value = data
    selectedVersion.value = data.version

    // Select default execution tab
    if (data.image && data.validated) {
      selectedExecutionTab.value = 'docker'
    } else if (data.cli && data.validated) {
      selectedExecutionTab.value = 'cli'
    } else if (data.pythonApi && data.validated) {
      selectedExecutionTab.value = 'python'
    }

    breadcrumbs.value = [
      { label: 'Home', to: '/', icon: 'i-lucide-home' },
      { label: 'Simulators', to: '/simulators' },
      { label: `${data.name} (v${data.version})`, to: '' },
    ]

    nextTick(() => {
      if (route.hash) {
        const el = document.querySelector(route.hash)
        if (el) el.scrollIntoView({ behavior: 'smooth' })
      }
    })
  } catch (err: any) {
    console.error('Error fetching simulator details:', err)
    error_encountered.value = err.message || 'Failed to load simulator details'
  } finally {
    loading.value = false
  }
}

watch(
  [simulatorId, currentVersionParam],
  () => {
    loadSimulator()
  },
  { immediate: false }
)

onMounted(() => {
  breadcrumbs.value = [
    { label: 'Home', to: '/', icon: 'i-lucide-home' },
    { label: 'Simulators', to: '/simulators' },
    { label: 'Simulator Details', to: '' },
  ]
  loadSimulator()
})
</script>

<template>
  <section
    class="w-full min-h-[calc(100vh-var(--ui-header-height))] p-6 max-w-(--ui-container) mx-auto flex flex-col gap-6"
    :class="{
      'items-center justify-center': loading || error_encountered,
      'items-start justify-start': !error_encountered && !loading,
    }"
  >
    <!-- Error State -->
    <template v-if="error_encountered">
      <ErrorPage
        message="An error occurred while fetching simulator details"
        :error="error_encountered"
        @refresh="loadSimulator"
      />
    </template>

    <!-- Loading State -->
    <template v-else-if="loading">
      <Loading class="mx-auto" message="Fetching simulator details..." />
    </template>

    <!-- Content State -->
    <template v-else-if="simulator">
      <UBreadcrumb class="mx-auto" :items="breadcrumbs" />

      <!-- Hero Header Banner (Consistent with runs/[id].vue and projects/[id].vue) -->
      <div class="page_header relative overflow-hidden w-full p-8 bg-primary-500 text-white flex flex-col items-center justify-center gap-3 rounded-xl shadow-md text-center">
        <div class="flex flex-wrap items-center justify-center gap-3">
          <h1 class="text-2xl sm:text-3xl font-bold tracking-tight">{{ simulator.name }}</h1>
          <UBadge color="neutral" variant="subtle" size="lg" class="font-semibold text-sm px-3 py-1">
            v{{ simulator.version }}
          </UBadge>
        </div>

        <p class="text-xs font-mono text-white/80 bg-white/10 px-3 py-1 rounded-full">
          ID: {{ simulator.id }}
        </p>

        <div v-if="simulator.description" class="max-w-3xl mt-1 text-white/90">
          <p class="text-sm leading-relaxed" :class="{ 'line-clamp-3': isDescriptionLong && !isDescriptionExpanded }">
            {{ simulator.description }}
          </p>
          <UButton
            v-if="isDescriptionLong"
            class="mt-2 bg-white text-primary-600 font-bold hover:bg-gray-100 transition-colors"
            size="xs"
            @click="isDescriptionExpanded = !isDescriptionExpanded"
          >
            {{ isDescriptionExpanded ? 'Show Less' : 'Show More' }}
          </UButton>
        </div>

        <!-- Authors Row -->
        <div v-if="simulator.authors.length > 0" class="flex flex-wrap items-center justify-center gap-2 mt-2 text-xs text-white/90">
          <span class="font-semibold text-white">Authors:</span>
          <span
            v-for="(author, idx) in simulator.authors"
            :key="idx"
            class="inline-flex items-center gap-1"
          >
            <span>{{ author.name }}</span>
            <a
              v-if="author.orcidUrl"
              :href="author.orcidUrl"
              target="_blank"
              rel="noopener noreferrer"
              title="ORCID profile"
              class="text-green-300 hover:text-white inline-flex items-center"
            >
              <UIcon name="i-lucide-award" class="w-3.5 h-3.5" />
            </a>
            <span v-if="idx < simulator.authors.length - 1">,</span>
          </span>
        </div>
      </div>

      <!-- Bento Trio: "Run", "Standardized", and "Details" -->
      <div class="w-full grid grid-cols-1 md:grid-cols-3 gap-6 items-stretch">
        <!-- Left: "Run" Card + "Standardized" Execution Card (Spans first 2 columns) -->
        <div class="md:col-span-2 w-full flex flex-col gap-6 min-w-0">
          <!-- 1. "Run" Online CTA Card -->
          <div class="w-full p-6 border border-primary-200 dark:border-primary-900/50 rounded-xl bg-primary-50/50 dark:bg-primary-950/20 shadow-sm flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
            <div class="flex items-center gap-3.5">
              <div class="p-3 bg-primary-500 text-white rounded-xl shadow-xs">
                <UIcon name="i-lucide-play" class="w-6 h-6" />
              </div>
              <div>
                <h3 class="text-base font-bold text-color">Run Simulations with {{ simulator.name }}</h3>
                <p class="text-xs text-muted mt-0.5">Execute models in COMBINE / OMEX format directly in the BioSimulations cloud.</p>
              </div>
            </div>
            <UButton
              :to="`/simulations/run?simulator=${encodeURIComponent(simulator.id)}&simulatorVersion=${encodeURIComponent(simulator.version)}`"
              color="primary"
              variant="solid"
              size="md"
              icon="i-lucide-play"
              label="Run Simulation"
              class="whitespace-nowrap cursor-pointer shadow-sm font-semibold"
            />
          </div>

          <!-- 2. "Standardized" Execution Interfaces Card (Docker / Singularity / CLI / Python) -->
          <div id="overview" class="w-full p-6 border border-neutral-200 dark:border-neutral-800 rounded-xl bg-neutral-50/50 dark:bg-neutral-900/50 shadow-sm flex flex-col gap-4 flex-1">
            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-neutral-200 dark:border-neutral-800 pb-4">
              <div class="flex items-center gap-2">
                <UIcon name="i-lucide-terminal" class="w-5 h-5 text-primary-500" />
                <h2 class="text-base font-bold text-color">Standardized Execution Interfaces</h2>
              </div>
              <div class="flex flex-wrap gap-1.5">
                <UButton
                  v-if="simulator.image && simulator.validated"
                  size="sm"
                  :color="selectedExecutionTab === 'docker' ? 'primary' : 'neutral'"
                  :variant="selectedExecutionTab === 'docker' ? 'solid' : 'ghost'"
                  icon="i-lucide-box"
                  label="Docker"
                  class="cursor-pointer font-medium"
                  @click="selectedExecutionTab = 'docker'"
                />
                <UButton
                  v-if="simulator.image && simulator.validated"
                  size="sm"
                  :color="selectedExecutionTab === 'singularity' ? 'primary' : 'neutral'"
                  :variant="selectedExecutionTab === 'singularity' ? 'solid' : 'ghost'"
                  icon="i-lucide-layers"
                  label="Singularity"
                  class="cursor-pointer font-medium"
                  @click="selectedExecutionTab = 'singularity'"
                />
                <UButton
                  v-if="simulator.cli && simulator.validated"
                  size="sm"
                  :color="selectedExecutionTab === 'cli' ? 'primary' : 'neutral'"
                  :variant="selectedExecutionTab === 'cli' ? 'solid' : 'ghost'"
                  icon="i-lucide-terminal"
                  label="CLI"
                  class="cursor-pointer font-medium"
                  @click="selectedExecutionTab = 'cli'"
                />
                <UButton
                  v-if="simulator.pythonApi && simulator.validated"
                  size="sm"
                  :color="selectedExecutionTab === 'python' ? 'primary' : 'neutral'"
                  :variant="selectedExecutionTab === 'python' ? 'solid' : 'ghost'"
                  icon="i-lucide-code"
                  label="Python API"
                  class="cursor-pointer font-medium"
                  @click="selectedExecutionTab = 'python'"
                />
              </div>
            </div>

            <!-- Docker Panel -->
            <div v-if="selectedExecutionTab === 'docker' && simulator.image" class="flex flex-col gap-4">
              <div class="flex items-center justify-between text-xs text-muted">
                <span class="inline-flex items-center gap-1.5 font-medium">
                  <UIcon name="i-lucide-info" class="w-4 h-4 text-primary-500" />
                  Standardized BioSimulators Docker Image
                </span>
                <a
                  :href="`https://github.com/orgs/biosimulators/packages/container/package/${simulator.id}`"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="text-primary-600 hover:underline inline-flex items-center gap-1 font-medium"
                >
                  <span>GHCR Repository</span>
                  <UIcon name="i-lucide-external-link" class="w-3.5 h-3.5" />
                </a>
              </div>

              <!-- Pull Command -->
              <div class="flex flex-col gap-1.5 min-w-0">
                <span class="text-xs font-semibold text-color">1. Pull Docker container:</span>
                <div class="relative group bg-neutral-900 text-neutral-100 p-3.5 rounded-lg font-mono text-xs overflow-x-auto max-w-full">
                  <code class="block pr-8 whitespace-pre">{{ simulator.image.url }}</code>
                  <UButton
                    size="xs"
                    color="neutral"
                    variant="ghost"
                    icon="i-lucide-copy"
                    class="absolute top-2.5 right-2.5 opacity-80 group-hover:opacity-100 cursor-pointer text-white hover:bg-neutral-800"
                    @click="copyText(`docker pull ${simulator.image.url}`, 'Docker pull command')"
                  />
                </div>
              </div>

              <!-- Run Command -->
              <div class="flex flex-col gap-1.5 min-w-0">
                <span class="text-xs font-semibold text-color">2. Run simulation with COMBINE archive:</span>
                <div class="relative group bg-neutral-900 text-neutral-100 p-3.5 rounded-lg font-mono text-xs overflow-x-auto max-w-full">
                  <code class="block pr-8 whitespace-pre">docker run -v /path/to/archive:/archive:ro -v /path/to/outputs:/outputs:rw {{ simulator.image.url }} -i /archive/model.omex -o /outputs</code>
                  <UButton
                    size="xs"
                    color="neutral"
                    variant="ghost"
                    icon="i-lucide-copy"
                    class="absolute top-2.5 right-2.5 opacity-80 group-hover:opacity-100 cursor-pointer text-white hover:bg-neutral-800"
                    @click="copyText(`docker run -v /path/to/archive:/archive:ro -v /path/to/outputs:/outputs:rw ${simulator.image.url} -i /archive/model.omex -o /outputs`, 'Docker run command')"
                  />
                </div>
              </div>

              <!-- Help Command -->
              <div class="flex flex-col gap-1.5 min-w-0">
                <span class="text-xs font-semibold text-color">3. Display help and CLI options:</span>
                <div class="relative group bg-neutral-900 text-neutral-100 p-3.5 rounded-lg font-mono text-xs overflow-x-auto max-w-full">
                  <code class="block pr-8 whitespace-pre">docker run {{ simulator.image.url }} --help</code>
                  <UButton
                    size="xs"
                    color="neutral"
                    variant="ghost"
                    icon="i-lucide-copy"
                    class="absolute top-2.5 right-2.5 opacity-80 group-hover:opacity-100 cursor-pointer text-white hover:bg-neutral-800"
                    @click="copyText(`docker run ${simulator.image.url} --help`, 'Docker help command')"
                  />
                </div>
              </div>
            </div>

            <!-- Singularity Panel -->
            <div v-else-if="selectedExecutionTab === 'singularity' && simulator.image" class="flex flex-col gap-4">
              <div class="flex items-center justify-between text-xs text-muted">
                <span class="inline-flex items-center gap-1.5 font-medium">
                  <UIcon name="i-lucide-info" class="w-4 h-4 text-primary-500" />
                  Standardized Singularity image for HPC execution
                </span>
                <span v-if="simulator.image.operatingSystemType" class="text-xs">
                  OS: {{ simulator.image.operatingSystemType }}
                </span>
              </div>

              <!-- Pull Singularity -->
              <div class="flex flex-col gap-1.5 min-w-0">
                <span class="text-xs font-semibold text-color">1. Pull Singularity image from Docker registry:</span>
                <div class="relative group bg-neutral-900 text-neutral-100 p-3.5 rounded-lg font-mono text-xs overflow-x-auto max-w-full">
                  <code class="block pr-8 whitespace-pre">singularity pull docker://{{ simulator.image.url }}</code>
                  <UButton
                    size="xs"
                    color="neutral"
                    variant="ghost"
                    icon="i-lucide-copy"
                    class="absolute top-2.5 right-2.5 opacity-80 group-hover:opacity-100 cursor-pointer text-white hover:bg-neutral-800"
                    @click="copyText(`singularity pull docker://${simulator.image.url}`, 'Singularity pull command')"
                  />
                </div>
              </div>

              <!-- Run Singularity -->
              <div class="flex flex-col gap-1.5 min-w-0">
                <span class="text-xs font-semibold text-color">2. Execute simulation via Singularity:</span>
                <div class="relative group bg-neutral-900 text-neutral-100 p-3.5 rounded-lg font-mono text-xs overflow-x-auto max-w-full">
                  <code class="block pr-8 whitespace-pre">singularity run -B /path/to/archive:/archive:ro -B /path/to/outputs:/outputs:rw docker://{{ simulator.image.url }} -i /archive/model.omex -o /outputs</code>
                  <UButton
                    size="xs"
                    color="neutral"
                    variant="ghost"
                    icon="i-lucide-copy"
                    class="absolute top-2.5 right-2.5 opacity-80 group-hover:opacity-100 cursor-pointer text-white hover:bg-neutral-800"
                    @click="copyText(`singularity run -B /path/to/archive:/archive:ro -B /path/to/outputs:/outputs:rw docker://${simulator.image.url} -i /archive/model.omex -o /outputs`, 'Singularity run command')"
                  />
                </div>
              </div>
            </div>

            <!-- CLI Panel -->
            <div v-else-if="selectedExecutionTab === 'cli' && simulator.cli" class="flex flex-col gap-4">
              <div class="flex items-center justify-between text-xs text-muted">
                <span class="inline-flex items-center gap-1.5 font-medium">
                  <UIcon name="i-lucide-info" class="w-4 h-4 text-primary-500" />
                  Standardized Command-Line Application (CLI)
                </span>
                <a
                  :href="simulator.cli.package?.startsWith('http') ? simulator.cli.package : `https://pypi.org/project/${simulator.cli.package}/`"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="text-primary-600 hover:underline inline-flex items-center gap-1 font-medium"
                >
                  <span>{{ simulator.cli.packageRepository || 'PyPI' }}: {{ simulator.cli.package }}</span>
                  <UIcon name="i-lucide-external-link" class="w-3.5 h-3.5" />
                </a>
              </div>

              <!-- Install CLI -->
              <div class="flex flex-col gap-1.5 min-w-0">
                <span class="text-xs font-semibold text-color">1. Install package:</span>
                <div class="relative group bg-neutral-900 text-neutral-100 p-3.5 rounded-lg font-mono text-xs overflow-x-auto max-w-full">
                  <code class="block pr-8 whitespace-pre">pip install {{ simulator.cli.package }}</code>
                  <UButton
                    size="xs"
                    color="neutral"
                    variant="ghost"
                    icon="i-lucide-copy"
                    class="absolute top-2.5 right-2.5 opacity-80 group-hover:opacity-100 cursor-pointer text-white hover:bg-neutral-800"
                    @click="copyText(`pip install ${simulator.cli.package}`, 'Pip install command')"
                  />
                </div>
              </div>

              <!-- Run CLI -->
              <div class="flex flex-col gap-1.5 min-w-0">
                <span class="text-xs font-semibold text-color">2. Execute simulation via CLI:</span>
                <div class="relative group bg-neutral-900 text-neutral-100 p-3.5 rounded-lg font-mono text-xs overflow-x-auto max-w-full">
                  <code class="block pr-8 whitespace-pre">{{ simulator.cli.command }} -i /path/to/archive.omex -o /path/to/outputs</code>
                  <UButton
                    size="xs"
                    color="neutral"
                    variant="ghost"
                    icon="i-lucide-copy"
                    class="absolute top-2.5 right-2.5 opacity-80 group-hover:opacity-100 cursor-pointer text-white hover:bg-neutral-800"
                    @click="copyText(`${simulator.cli.command} -i /path/to/archive.omex -o /path/to/outputs`, 'CLI run command')"
                  />
                </div>
              </div>
            </div>

            <!-- Python API Panel -->
            <div v-else-if="selectedExecutionTab === 'python' && simulator.pythonApi" class="flex flex-col gap-4">
              <div class="flex items-center justify-between text-xs text-muted">
                <span class="inline-flex items-center gap-1.5 font-medium">
                  <UIcon name="i-lucide-info" class="w-4 h-4 text-primary-500" />
                  Standardized Python API Module
                </span>
                <a
                  :href="simulator.pythonApi.package?.startsWith('http') ? simulator.pythonApi.package : `https://pypi.org/project/${simulator.pythonApi.package}/`"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="text-primary-600 hover:underline inline-flex items-center gap-1 font-medium"
                >
                  <span>PyPI: {{ simulator.pythonApi.package }}</span>
                  <UIcon name="i-lucide-external-link" class="w-3.5 h-3.5" />
                </a>
              </div>

              <!-- Install Python -->
              <div class="flex flex-col gap-1.5 min-w-0">
                <span class="text-xs font-semibold text-color">1. Install Python package:</span>
                <div class="relative group bg-neutral-900 text-neutral-100 p-3.5 rounded-lg font-mono text-xs overflow-x-auto max-w-full">
                  <code class="block pr-8 whitespace-pre">pip install {{ simulator.pythonApi.package }}</code>
                  <UButton
                    size="xs"
                    color="neutral"
                    variant="ghost"
                    icon="i-lucide-copy"
                    class="absolute top-2.5 right-2.5 opacity-80 group-hover:opacity-100 cursor-pointer text-white hover:bg-neutral-800"
                    @click="copyText(`pip install ${simulator.pythonApi.package}`, 'Pip install command')"
                  />
                </div>
              </div>

              <!-- Run Python Code -->
              <div class="flex flex-col gap-1.5 min-w-0">
                <span class="text-xs font-semibold text-color">2. Python code snippet:</span>
                <div class="relative group bg-neutral-900 text-neutral-100 p-3.5 rounded-lg font-mono text-xs overflow-x-auto max-w-full">
                  <code class="block pr-8 whitespace-pre">import {{ simulator.pythonApi.module }} as simulator
simulator.exec_sedml_docs_in_combine_archive('/path/to/archive.omex', '/path/to/outputs')</code>
                  <UButton
                    size="xs"
                    color="neutral"
                    variant="ghost"
                    icon="i-lucide-copy"
                    class="absolute top-2.5 right-2.5 opacity-80 group-hover:opacity-100 cursor-pointer text-white hover:bg-neutral-800"
                    @click="copyText(`import ${simulator.pythonApi.module} as simulator\nsimulator.exec_sedml_docs_in_combine_archive('/path/to/archive.omex', '/path/to/outputs')`, 'Python code snippet')"
                  />
                </div>
              </div>
            </div>

            <!-- Fallback if no standardized interfaces -->
            <div v-else class="text-xs text-muted p-4 text-center">
              No standardized container or CLI interface has been curated for this version.
            </div>
          </div>
        </div>

        <!-- 3. "Details" Panel (Spans 3rd column, aligning with the 3-column row below) -->
        <div class="metadata_panel md:col-span-1 w-full flex flex-col justify-between p-6 rounded-xl border border-neutral-300 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-900 shadow-sm min-w-0">
          <div class="flex flex-col">
            <div class="w-full flex items-center justify-between gap-2">
              <div class="flex items-center gap-2">
                <UIcon name="i-lucide-info" class="w-4 h-4 text-primary-500" />
                <p class="font-bold text-color text-sm">Simulator Details</p>
              </div>
              <UBadge
                :color="simulator.validated ? 'success' : 'neutral'"
                variant="subtle"
                size="sm"
                class="font-semibold text-xs px-2.5 py-0.5"
              >
                {{ simulator.validated ? 'Validated' : 'Registered' }}
              </UBadge>
            </div>

            <USeparator color="neutral" class="my-3.5" />

            <table class="w-full text-xs border-collapse">
              <tbody class="divide-y divide-neutral-200 dark:divide-neutral-800">
                <!-- Version Selector Row -->
                <tr>
                  <td class="py-2.5 font-bold text-color pr-3 w-28">Version:</td>
                  <td class="py-2.5">
                    <USelectMenu
                      v-if="simulator.versions.length > 0"
                      v-model="selectedVersion"
                      :items="simulator.versions.map(v => v.version)"
                      class="w-full"
                      size="xs"
                      @update:model-value="onVersionSelect"
                    />
                    <span v-else class="font-mono">v{{ simulator.version }}</span>
                  </td>
                </tr>

                <!-- Curation Rating (NO BADGE) -->
                <tr>
                  <td class="py-2.5 font-bold text-color pr-3">Curation:</td>
                  <td class="py-2.5">
                    <UTooltip :text="simulator.curationStatusMessage">
                      <div class="flex items-center gap-1.5 cursor-default select-none">
                        <span class="text-amber-500 font-bold text-base tracking-wider">
                          {{ '★'.repeat(simulator.curationStatus) + '☆'.repeat(5 - simulator.curationStatus) }}
                        </span>
                        <span class="text-xs text-muted font-medium">({{ simulator.curationStatus }}/5)</span>
                      </div>
                    </UTooltip>
                  </td>
                </tr>

                <!-- License -->
                <tr>
                  <td class="py-2.5 font-bold text-color pr-3">License:</td>
                  <td class="py-2.5">
                    <a
                      v-if="simulator.licenseUrl"
                      :href="simulator.licenseUrl"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="text-primary-600 hover:underline font-medium inline-flex items-center gap-1"
                    >
                      <span>{{ simulator.licenseName || 'View License' }}</span>
                      <UIcon name="i-lucide-external-link" class="w-3 h-3" />
                    </a>
                    <span v-else class="text-muted">{{ simulator.licenseName || 'N/A' }}</span>
                  </td>
                </tr>

                <!-- Registered Date -->
                <tr>
                  <td class="py-2.5 font-bold text-color pr-3">Registered:</td>
                  <td class="py-2.5 text-muted">
                    <NuxtTime v-if="simulator.created" :datetime="simulator.created" />
                    <span v-else>N/A</span>
                  </td>
                </tr>

                <!-- Last Updated Date -->
                <tr>
                  <td class="py-2.5 font-bold text-color pr-3">Updated:</td>
                  <td class="py-2.5 text-muted">
                    <NuxtTime v-if="simulator.updated" :datetime="simulator.updated" />
                    <span v-else>N/A</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- Action Buttons -->
          <div class="mt-5 pt-4 border-t border-neutral-200 dark:border-neutral-800 flex flex-col gap-2">
            <UButton
              :to="`/simulations/run?simulator=${encodeURIComponent(simulator.id)}&simulatorVersion=${encodeURIComponent(simulator.version)}`"
              color="primary"
              variant="solid"
              size="md"
              icon="i-lucide-play"
              label="Run Simulation"
              block
              class="cursor-pointer font-semibold shadow-xs"
            />

            <UButton
              v-if="simulator.urls.length > 0 && simulator.urls[0]?.url"
              :to="simulator.urls[0].url"
              target="_blank"
              color="neutral"
              variant="outline"
              size="sm"
              icon="i-lucide-globe"
              :label="simulator.urls[0].title || 'Home Page'"
              block
              class="cursor-pointer font-medium"
            />

            <UButton
              v-if="simulator.image"
              :to="`https://github.com/orgs/biosimulators/packages/container/package/${simulator.id}`"
              target="_blank"
              color="neutral"
              variant="outline"
              size="sm"
              icon="i-lucide-box"
              label="Docker Image (GHCR)"
              block
              class="cursor-pointer font-medium"
            />

            <UButton
              v-if="simulator.cli || simulator.pythonApi"
              :to="`https://pypi.org/project/${simulator.cli?.package || simulator.pythonApi?.package}/`"
              target="_blank"
              color="neutral"
              variant="outline"
              size="sm"
              icon="i-lucide-terminal"
              label="PyPI Package"
              block
              class="cursor-pointer font-medium"
            />

            <UButton
              color="neutral"
              variant="ghost"
              size="sm"
              icon="i-lucide-share-2"
              label="Copy Page Link"
              block
              class="cursor-pointer font-medium"
              @click="copyText(`${runtimeConfig.public.base_url || ''}/simulators/${simulator.id}/${simulator.version}`, 'Simulator link')"
            />
          </div>
        </div>
      </div>

      <!-- Full-Width Bento Row: "Supported OS", "Languages", and "Interfaces" Trio -->
      <div class="w-full grid grid-cols-1 md:grid-cols-3 gap-6">
        <!-- Supported OS Card -->
        <div class="p-5 border border-neutral-200 dark:border-neutral-800 rounded-xl bg-neutral-50/50 dark:bg-neutral-900/50 shadow-xs flex flex-col gap-3">
          <span class="text-sm font-bold text-color flex items-center gap-2">
            <UIcon name="i-lucide-monitor" class="w-4 h-4 text-primary-500" />
            Supported Operating Systems
          </span>
          <div class="flex flex-wrap gap-1.5">
            <UBadge
              v-for="os in simulator.supportedOperatingSystemTypes"
              :key="os"
              color="neutral"
              variant="subtle"
              size="md"
              class="text-xs font-medium px-2.5 py-1"
            >
              {{ os }}
            </UBadge>
            <span v-if="simulator.supportedOperatingSystemTypes.length === 0" class="text-xs text-muted">
              Not specified
            </span>
          </div>
        </div>

        <!-- Programming Languages Card -->
        <div class="p-5 border border-neutral-200 dark:border-neutral-800 rounded-xl bg-neutral-50/50 dark:bg-neutral-900/50 shadow-xs flex flex-col gap-3">
          <span class="text-sm font-bold text-color flex items-center gap-2">
            <UIcon name="i-lucide-code" class="w-4 h-4 text-primary-500" />
            Supported Programming Languages
          </span>
          <div class="flex flex-wrap gap-1.5">
            <UBadge
              v-for="lang in simulator.supportedProgrammingLanguages"
              :key="lang"
              color="primary"
              variant="subtle"
              size="md"
              class="text-xs font-medium px-2.5 py-1"
            >
              {{ lang }}
            </UBadge>
            <span v-if="simulator.supportedProgrammingLanguages.length === 0" class="text-xs text-muted">
              Not specified
            </span>
          </div>
        </div>

        <!-- Other Interfaces Card -->
        <div class="p-5 border border-neutral-200 dark:border-neutral-800 rounded-xl bg-neutral-50/50 dark:bg-neutral-900/50 shadow-xs flex flex-col gap-3">
          <span class="text-sm font-bold text-color flex items-center gap-2">
            <UIcon name="i-lucide-layout-grid" class="w-4 h-4 text-primary-500" />
            Supported Software Interfaces
          </span>
          <div class="flex flex-wrap gap-1.5">
            <UBadge
              v-for="iface in simulator.otherInterfaceTypes"
              :key="iface"
              color="neutral"
              variant="outline"
              size="md"
              class="text-xs font-medium px-2.5 py-1"
            >
              {{ iface }}
            </UBadge>
            <span v-if="simulator.otherInterfaceTypes.length === 0" class="text-xs text-muted">
              None curated
            </span>
          </div>
        </div>
      </div>

      <!-- Section 1: Algorithms & Modeling Frameworks (#algorithms) -->
      <div id="algorithms" class="w-full p-6 border border-neutral-200 dark:border-neutral-800 rounded-xl bg-neutral-50/50 dark:bg-neutral-900/50 shadow-sm">
        <UCollapsible class="w-full flex flex-col gap-2" :default-open="true">
          <UButton
            class="group w-full text-lg font-bold text-color cursor-pointer p-0"
            :label="`Algorithms (${simulator.algorithms.length})`"
            color="neutral"
            variant="link"
            trailing-icon="i-lucide-chevron-down"
            :ui="{ trailingIcon: 'group-data-[state=open]:rotate-180 transition-transform duration-200' }"
            block
          />

          <template #content>
            <div class="mt-4 flex flex-col gap-6">
              <div v-if="simulator.algorithms.length === 0" class="p-6 text-center text-muted bg-neutral-100 dark:bg-neutral-800 rounded-lg">
                <p>The algorithms implemented by this simulator have not yet been curated.</p>
              </div>

              <!-- Algorithm Cards -->
              <div
                v-for="alg in simulator.algorithms"
                :key="alg.id"
                class="p-5 border border-neutral-200 dark:border-neutral-800 rounded-xl bg-white dark:bg-neutral-900 flex flex-col gap-4 shadow-xs"
              >
                <!-- Alg Header -->
                <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-neutral-200 dark:border-neutral-800 pb-3">
                  <div>
                    <h3 class="text-base font-bold text-color">{{ alg.name }}</h3>
                    <a
                      :href="alg.kisaoUrl"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="text-xs font-mono text-primary-600 hover:underline inline-flex items-center gap-1 mt-0.5"
                    >
                      <span>{{ alg.kisaoId }}</span>
                      <UIcon name="i-lucide-external-link" class="w-3 h-3" />
                    </a>
                  </div>
                </div>

                <!-- Alg Description -->
                <p v-if="alg.description" class="text-xs text-muted leading-relaxed">
                  {{ alg.description }}
                </p>

                <!-- Frameworks & Formats Grid -->
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
                  <!-- Modeling Frameworks -->
                  <div class="flex flex-col gap-2 p-3.5 rounded-lg bg-neutral-50 dark:bg-neutral-800/60 border border-neutral-200/60 dark:border-neutral-700/60">
                    <span class="font-bold text-color">Modeling Frameworks:</span>
                    <div class="flex flex-wrap gap-1.5 mt-0.5">
                      <a
                        v-for="fw in alg.modelingFrameworks"
                        :key="fw.id"
                        :href="fw.url"
                        target="_blank"
                        rel="noopener noreferrer"
                        class="inline-block"
                      >
                        <UBadge color="primary" variant="subtle" size="md" class="text-xs font-medium px-2.5 py-1 hover:bg-primary-100 dark:hover:bg-primary-900/40">
                          {{ fw.name }}
                        </UBadge>
                      </a>
                      <span v-if="alg.modelingFrameworks.length === 0" class="text-muted">
                        None curated
                      </span>
                    </div>
                  </div>

                  <!-- Model Formats -->
                  <div class="flex flex-col gap-2 p-3.5 rounded-lg bg-neutral-50 dark:bg-neutral-800/60 border border-neutral-200/60 dark:border-neutral-700/60">
                    <span class="font-bold text-color">Supported Model Formats:</span>
                    <div class="flex flex-wrap gap-1.5 mt-0.5">
                      <a
                        v-for="mf in alg.modelFormats"
                        :key="mf.id"
                        :href="mf.url"
                        target="_blank"
                        rel="noopener noreferrer"
                        class="inline-block"
                      >
                        <UBadge color="neutral" variant="outline" size="md" class="text-xs font-medium px-2.5 py-1 hover:bg-neutral-100 dark:hover:bg-neutral-800">
                          {{ mf.name }} {{ mf.version ? `(${mf.version})` : '' }}
                        </UBadge>
                      </a>
                      <span v-if="alg.modelFormats.length === 0" class="text-muted">
                        None curated
                      </span>
                    </div>
                  </div>

                  <!-- Simulation Types -->
                  <div class="flex flex-col gap-2 p-3.5 rounded-lg bg-neutral-50 dark:bg-neutral-800/60 border border-neutral-200/60 dark:border-neutral-700/60">
                    <span class="font-bold text-color">Simulation Types:</span>
                    <div class="flex flex-wrap gap-1.5 mt-0.5">
                      <UBadge
                        v-for="st in alg.simulationTypes"
                        :key="st.value"
                        color="neutral"
                        variant="subtle"
                        size="md"
                        class="text-xs font-medium px-2.5 py-1"
                      >
                        {{ st.name }}
                      </UBadge>
                      <span v-if="alg.simulationTypes.length === 0" class="text-muted">
                        None curated
                      </span>
                    </div>
                  </div>

                  <!-- Archive Formats -->
                  <div class="flex flex-col gap-2 p-3.5 rounded-lg bg-neutral-50 dark:bg-neutral-800/60 border border-neutral-200/60 dark:border-neutral-700/60">
                    <span class="font-bold text-color">Archive Formats:</span>
                    <div class="flex flex-wrap gap-1.5 mt-0.5">
                      <a
                        v-for="af in alg.archiveFormats"
                        :key="af.id"
                        :href="af.url"
                        target="_blank"
                        rel="noopener noreferrer"
                        class="inline-block"
                      >
                        <UBadge color="neutral" variant="outline" size="md" class="text-xs font-medium px-2.5 py-1 hover:bg-neutral-100 dark:hover:bg-neutral-800">
                          {{ af.name }}
                        </UBadge>
                      </a>
                      <span v-if="alg.archiveFormats.length === 0" class="text-muted">
                        None curated
                      </span>
                    </div>
                  </div>
                </div>

                <!-- Parameters Subtable -->
                <div v-if="alg.parameters.length > 0" class="flex flex-col gap-2 mt-2">
                  <span class="text-xs font-bold text-color">Parameters ({{ alg.parameters.length }}):</span>
                  <div class="overflow-x-auto border border-neutral-200 dark:border-neutral-800 rounded-lg">
                    <table class="w-full text-xs border-collapse">
                      <thead class="bg-neutral-100 dark:bg-neutral-800 text-left font-semibold text-color">
                        <tr>
                          <th class="p-3">Name</th>
                          <th class="p-3">KiSAO ID</th>
                          <th class="p-3">Type</th>
                          <th class="p-3">Default Value</th>
                          <th class="p-3">Recommended Range / Values</th>
                        </tr>
                      </thead>
                      <tbody class="divide-y divide-neutral-200 dark:divide-neutral-800">
                        <tr v-for="param in alg.parameters" :key="param.id" class="hover:bg-neutral-50 dark:hover:bg-neutral-850">
                          <td class="p-3 font-medium text-color">{{ param.name }}</td>
                          <td class="p-3 font-mono">
                            <a
                              :href="param.kisaoUrl"
                              target="_blank"
                              rel="noopener noreferrer"
                              class="text-primary-600 hover:underline"
                            >
                              {{ param.kisaoId }}
                            </a>
                          </td>
                          <td class="p-3"><span class="font-mono text-muted">{{ param.type }}</span></td>
                          <td class="p-3 font-mono">{{ param.formattedValue ?? 'null' }}</td>
                          <td class="p-3 text-muted">
                            <span v-if="param.formattedRange && param.formattedRange.length > 0">
                              [{{ param.formattedRange.join(', ') }}]
                            </span>
                            <span v-else>None specified</span>
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>

                <!-- Citations for this Algorithm -->
                <div v-if="alg.citations.length > 0" class="flex flex-col gap-1.5 mt-2">
                  <span class="text-xs font-bold text-color">Algorithm Citations:</span>
                  <ul class="list-disc list-inside text-xs text-muted flex flex-col gap-1">
                    <li v-for="(cit, idx) in alg.citations" :key="idx">
                      <span v-dompurify-html="cit.text"></span>
                      <a
                        v-if="cit.url"
                        :href="cit.url"
                        target="_blank"
                        rel="noopener noreferrer"
                        class="text-primary-600 hover:underline ml-1 inline-flex items-center gap-0.5 font-medium"
                      >
                        <span>[Link]</span>
                        <UIcon name="i-lucide-external-link" class="w-3 h-3" />
                      </a>
                    </li>
                  </ul>
                </div>
              </div>
            </div>
          </template>
        </UCollapsible>
      </div>

      <!-- Section 2: Version History (#versions) -->
      <div id="versions" class="w-full p-6 border border-neutral-200 dark:border-neutral-800 rounded-xl bg-neutral-50/50 dark:bg-neutral-900/50 shadow-sm">
        <UCollapsible class="w-full flex flex-col gap-2" :default-open="true">
          <UButton
            class="group w-full text-lg font-bold text-color cursor-pointer p-0"
            :label="`Versions (${simulator.versions.length})`"
            color="neutral"
            variant="link"
            trailing-icon="i-lucide-chevron-down"
            :ui="{ trailingIcon: 'group-data-[state=open]:rotate-180 transition-transform duration-200' }"
            block
          />

          <template #content>
            <div class="mt-4 overflow-x-auto border border-neutral-200 dark:border-neutral-800 rounded-lg">
              <table class="w-full text-xs border-collapse">
                <thead class="bg-neutral-100 dark:bg-neutral-800 text-left font-semibold text-color">
                  <tr>
                    <th class="p-3.5">Version</th>
                    <th class="p-3.5">Released Date</th>
                    <th class="p-3.5">Docker Image Digest</th>
                    <th class="p-3.5">Curation</th>
                    <th class="p-3.5">Validated</th>
                    <th class="p-3.5 text-right">Action</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-neutral-200 dark:divide-neutral-800">
                  <tr
                    v-for="v in simulator.versions"
                    :key="v.version"
                    class="hover:bg-neutral-50 dark:hover:bg-neutral-850"
                    :class="{ 'bg-primary-50/40 dark:bg-primary-950/20': v.version === simulator.version }"
                  >
                    <td class="p-3.5 font-semibold text-color flex items-center gap-2">
                      <span class="font-mono text-xs">v{{ v.version }}</span>
                      <UBadge
                        v-if="v.version === simulator.version"
                        color="primary"
                        variant="solid"
                        size="sm"
                        class="text-xs font-medium px-2 py-0.5"
                      >
                        Current
                      </UBadge>
                    </td>
                    <td class="p-3.5 text-muted">
                      {{ formatDate(v.created) }}
                    </td>
                    <td class="p-3.5 font-mono text-muted max-w-xs truncate" :title="v.image?.digest || ''">
                      {{ v.image?.digest ? v.image.digest.slice(0, 19) + '...' : 'None' }}
                    </td>

                    <!-- Curation Rating in Table (NO BADGE) -->
                    <td class="p-3.5">
                      <UTooltip :text="v.curationStatusMessage">
                        <span class="text-amber-500 font-bold text-sm tracking-wider cursor-default select-none">
                          {{ '★'.repeat(v.curationStatus) + '☆'.repeat(5 - v.curationStatus) }}
                        </span>
                      </UTooltip>
                    </td>

                    <!-- Validated Badge -->
                    <td class="p-3.5">
                      <UBadge
                        :color="v.validated ? 'success' : 'neutral'"
                        variant="subtle"
                        size="sm"
                        class="text-xs font-medium px-2.5 py-0.5"
                      >
                        {{ v.validated ? 'Passed' : 'No' }}
                      </UBadge>
                    </td>
                    <td class="p-3.5 text-right">
                      <UButton
                        v-if="v.version !== simulator.version"
                        :to="`/simulators/${simulator.id}/${v.version}`"
                        size="xs"
                        color="primary"
                        variant="outline"
                        label="View Version"
                        class="cursor-pointer"
                      />
                      <span v-else class="text-xs text-muted font-medium">Active</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>
        </UCollapsible>
      </div>

      <!-- Section 3: Validation Test Results (#validation) -->
      <div id="validation" class="w-full p-6 border border-neutral-200 dark:border-neutral-800 rounded-xl bg-neutral-50/50 dark:bg-neutral-900/50 shadow-sm">
        <UCollapsible class="w-full flex flex-col gap-2" :default-open="true">
          <UButton
            class="group w-full text-lg font-bold text-color cursor-pointer p-0"
            label="Validation Test Suite"
            color="neutral"
            variant="link"
            trailing-icon="i-lucide-chevron-down"
            :ui="{ trailingIcon: 'group-data-[state=open]:rotate-180 transition-transform duration-200' }"
            block
          />

          <template #content>
            <div class="mt-4 flex flex-col gap-4">
              <!-- Summary Statistics Card -->
              <div v-if="simulator.validationTests" class="p-5 rounded-xl border border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900 flex flex-col gap-4 shadow-xs">
                <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-neutral-200 dark:border-neutral-800 pb-3">
                  <div>
                    <h3 class="text-sm font-bold text-color">BioSimulators Test Suite Results</h3>
                    <div class="flex items-center gap-2 mt-0.5 text-xs text-muted">
                      <span>Test Suite:</span>
                      <a
                        :href="simulator.validationTests.testSuiteVersionUrl"
                        target="_blank"
                        rel="noopener noreferrer"
                        class="font-mono text-primary-600 hover:underline inline-flex items-center gap-1 font-medium"
                      >
                        <span>v{{ simulator.validationTests.testSuiteVersion }}</span>
                        <UIcon name="i-lucide-external-link" class="w-3 h-3" />
                      </a>
                    </div>
                  </div>

                  <div class="flex flex-wrap items-center gap-2">
                    <UButton
                      v-if="simulator.validationTests.ghIssueUrl"
                      :to="simulator.validationTests.ghIssueUrl"
                      target="_blank"
                      size="xs"
                      color="neutral"
                      variant="outline"
                      icon="i-lucide-alert-circle"
                      :label="`Issue #${simulator.validationTests.ghIssue}`"
                      class="cursor-pointer font-medium"
                    />
                    <UButton
                      v-if="simulator.validationTests.ghActionRunUrl"
                      :to="simulator.validationTests.ghActionRunUrl"
                      target="_blank"
                      size="xs"
                      color="neutral"
                      variant="outline"
                      icon="i-lucide-play-circle"
                      label="Action Run"
                      class="cursor-pointer font-medium"
                    />
                    <UButton
                      to="https://docs.biosimulators.org/Biosimulators_test_suite/"
                      target="_blank"
                      size="xs"
                      color="neutral"
                      variant="ghost"
                      icon="i-lucide-book-open"
                      label="Test Suite Docs"
                      class="cursor-pointer font-medium"
                    />
                  </div>
                </div>

                <!-- Metrics Pills -->
                <div class="grid grid-cols-2 sm:grid-cols-5 gap-3 text-center">
                  <div class="p-3 rounded-lg bg-neutral-100 dark:bg-neutral-800">
                    <p class="text-xl font-bold text-color">{{ simulator.validationTests.numTests }}</p>
                    <p class="text-xs text-muted font-medium mt-0.5">Total Tests</p>
                  </div>
                  <div class="p-3 rounded-lg bg-green-50 dark:bg-green-950/30 text-green-700 dark:text-green-400">
                    <p class="text-xl font-bold">{{ simulator.validationTests.numTestsPassed }}</p>
                    <p class="text-xs font-medium mt-0.5">Passed</p>
                  </div>
                  <div class="p-3 rounded-lg bg-yellow-50 dark:bg-yellow-950/30 text-yellow-700 dark:text-yellow-400">
                    <p class="text-xl font-bold">{{ simulator.validationTests.numTestsPassedWithWarnings }}</p>
                    <p class="text-xs font-medium mt-0.5">With Warnings</p>
                  </div>
                  <div class="p-3 rounded-lg bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-400">
                    <p class="text-xl font-bold">{{ simulator.validationTests.numTestsSkipped }}</p>
                    <p class="text-xs font-medium mt-0.5">Skipped</p>
                  </div>
                  <div class="p-3 rounded-lg bg-red-50 dark:bg-red-950/30 text-red-700 dark:text-red-400">
                    <p class="text-xl font-bold">{{ simulator.validationTests.numTestsFailed }}</p>
                    <p class="text-xs font-medium mt-0.5">Failed</p>
                  </div>
                </div>

                <!-- Test Filter Bar -->
                <div class="flex flex-col sm:flex-row items-center justify-between gap-3 pt-2">
                  <div class="flex flex-wrap gap-1.5 w-full sm:w-auto">
                    <UButton
                      size="sm"
                      :color="activeTestFilter === 'all' ? 'primary' : 'neutral'"
                      :variant="activeTestFilter === 'all' ? 'solid' : 'outline'"
                      label="All Tests"
                      class="cursor-pointer font-medium"
                      @click="activeTestFilter = 'all'"
                    />
                    <UButton
                      size="sm"
                      :color="activeTestFilter === 'Passed' ? 'success' : 'neutral'"
                      :variant="activeTestFilter === 'Passed' ? 'solid' : 'outline'"
                      label="Passed"
                      class="cursor-pointer font-medium"
                      @click="activeTestFilter = 'Passed'"
                    />
                    <UButton
                      size="sm"
                      :color="activeTestFilter === 'Warned' ? 'warning' : 'neutral'"
                      :variant="activeTestFilter === 'Warned' ? 'solid' : 'outline'"
                      label="Warnings"
                      class="cursor-pointer font-medium"
                      @click="activeTestFilter = 'Warned'"
                    />
                    <UButton
                      size="sm"
                      :color="activeTestFilter === 'Skipped' ? 'neutral' : 'neutral'"
                      :variant="activeTestFilter === 'Skipped' ? 'solid' : 'outline'"
                      label="Skipped"
                      class="cursor-pointer font-medium"
                      @click="activeTestFilter = 'Skipped'"
                    />
                    <UButton
                      size="sm"
                      :color="activeTestFilter === 'Failed' ? 'error' : 'neutral'"
                      :variant="activeTestFilter === 'Failed' ? 'solid' : 'outline'"
                      label="Failed"
                      class="cursor-pointer font-medium"
                      @click="activeTestFilter = 'Failed'"
                    />
                  </div>

                  <UInput
                    v-model="testSearchQuery"
                    placeholder="Search test cases..."
                    icon="i-lucide-search"
                    size="sm"
                    class="w-full sm:w-64"
                  />
                </div>

                <!-- Test Cases List -->
                <div class="flex flex-col divide-y divide-neutral-200 dark:divide-neutral-800 border border-neutral-200 dark:border-neutral-800 rounded-lg overflow-hidden mt-1">
                  <div
                    v-for="res in filteredTestResults"
                    :key="res.caseId"
                    class="p-4 flex flex-col gap-2 hover:bg-neutral-50/50 dark:hover:bg-neutral-850 transition-colors"
                  >
                    <div class="flex items-start justify-between gap-3">
                      <div class="flex items-start gap-3">
                        <UBadge
                          :color="res.resultType === 'Passed' ? 'success' : res.resultType === 'Warned' ? 'warning' : res.resultType === 'Skipped' ? 'neutral' : 'error'"
                          variant="solid"
                          size="sm"
                          class="mt-0.5 whitespace-nowrap font-semibold text-xs px-2.5 py-0.5"
                        >
                          {{ res.resultType }}
                        </UBadge>
                        <div class="flex flex-col">
                          <div class="flex items-center gap-2">
                            <span class="font-mono font-semibold text-xs text-color">{{ res.caseId }}</span>
                            <span class="text-xs text-muted">({{ res.duration }})</span>
                          </div>
                          <p class="text-xs text-muted mt-0.5" v-dompurify-html="res.description"></p>
                        </div>
                      </div>

                      <div class="flex items-center gap-1.5 shrink-0">
                        <a
                          v-if="res.caseArchiveUrl"
                          :href="res.caseArchiveUrl"
                          target="_blank"
                          rel="noopener noreferrer"
                          title="Download sample OMEX archive"
                          class="p-1.5 rounded-md hover:bg-neutral-200 dark:hover:bg-neutral-700 text-neutral-600 dark:text-neutral-300 transition-colors"
                        >
                          <UIcon name="i-lucide-download" class="w-4 h-4" />
                        </a>
                        <a
                          :href="res.caseUrl"
                          target="_blank"
                          rel="noopener noreferrer"
                          title="View test case source code"
                          class="p-1.5 rounded-md hover:bg-neutral-200 dark:hover:bg-neutral-700 text-neutral-600 dark:text-neutral-300 transition-colors"
                        >
                          <UIcon name="i-lucide-code" class="w-4 h-4" />
                        </a>
                        <UButton
                          v-if="res.log || res.exception || res.warnings.length > 0 || res.skipReason"
                          size="xs"
                          color="neutral"
                          variant="ghost"
                          :icon="expandedTestLogs[res.caseId] ? 'i-lucide-chevron-up' : 'i-lucide-chevron-down'"
                          class="cursor-pointer"
                          @click="toggleTestLog(res.caseId)"
                        />
                      </div>
                    </div>

                    <!-- Expanded Test Details -->
                    <div
                      v-if="expandedTestLogs[res.caseId]"
                      class="mt-2 p-3.5 bg-neutral-100 dark:bg-neutral-800 rounded-lg text-xs flex flex-col gap-2.5 font-mono"
                    >
                      <!-- Skip Reason -->
                      <div v-if="res.skipReason" class="text-neutral-600 dark:text-neutral-300">
                        <span class="font-bold text-neutral-800 dark:text-neutral-100">Skip Reason:</span>
                        <p class="mt-0.5 whitespace-pre-wrap">{{ res.skipReason.message }}</p>
                      </div>

                      <!-- Exception / Failure -->
                      <div v-if="res.exception" class="text-red-600 dark:text-red-400">
                        <span class="font-bold">Failure Exception ({{ res.exception.category }}):</span>
                        <p class="mt-0.5 whitespace-pre-wrap">{{ res.exception.message }}</p>
                      </div>

                      <!-- Warnings -->
                      <div v-if="res.warnings.length > 0" class="text-yellow-700 dark:text-yellow-400">
                        <span class="font-bold">Warnings:</span>
                        <ul class="list-disc list-inside mt-0.5">
                          <li v-for="(w, idx) in res.warnings" :key="idx">
                            {{ w.message }}
                          </li>
                        </ul>
                      </div>

                      <!-- Log Output -->
                      <div v-if="res.log">
                        <span class="font-bold text-neutral-700 dark:text-neutral-300">Test Execution Log:</span>
                        <pre class="mt-1 p-2.5 bg-neutral-900 text-neutral-200 rounded-lg overflow-x-auto text-[11px] leading-relaxed max-h-60">{{ res.log }}</pre>
                      </div>
                    </div>
                  </div>

                  <div v-if="filteredTestResults.length === 0" class="p-6 text-center text-xs text-muted">
                    No test results matched your filter criteria.
                  </div>
                </div>
              </div>

              <!-- Unvalidated Notice -->
              <div v-else class="p-6 text-center text-muted bg-neutral-100 dark:bg-neutral-800 rounded-lg">
                <p>No validation test results are available for this simulator version.</p>
              </div>
            </div>
          </template>
        </UCollapsible>
      </div>

      <!-- Section 4: Citations & Cross References (#citations) -->
      <div id="citations" class="w-full p-6 border border-neutral-200 dark:border-neutral-800 rounded-xl bg-neutral-50/50 dark:bg-neutral-900/50 shadow-sm">
        <UCollapsible class="w-full flex flex-col gap-2" :default-open="true">
          <UButton
            class="group w-full text-lg font-bold text-color cursor-pointer p-0"
            label="Citations, Identifiers & Funding"
            color="neutral"
            variant="link"
            trailing-icon="i-lucide-chevron-down"
            :ui="{ trailingIcon: 'group-data-[state=open]:rotate-180 transition-transform duration-200' }"
            block
          />

          <template #content>
            <div class="mt-4 grid grid-cols-1 md:grid-cols-3 gap-6 text-xs">
              <!-- Citations -->
              <div class="flex flex-col gap-2">
                <h4 class="font-bold text-color text-sm flex items-center gap-1.5">
                  <UIcon name="i-lucide-book-open" class="w-4 h-4 text-primary-500" />
                  Citations
                </h4>
                <ul v-if="simulator.citations.length > 0" class="flex flex-col gap-2.5 text-muted leading-relaxed">
                  <li v-for="(cit, idx) in simulator.citations" :key="idx" class="p-3 rounded-lg bg-neutral-100/60 dark:bg-neutral-800/60 border border-neutral-200/60 dark:border-neutral-700/60">
                    <span v-dompurify-html="cit.text"></span>
                    <a
                      v-if="cit.url"
                      :href="cit.url"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="text-primary-600 hover:underline ml-1 inline-flex items-center gap-0.5 font-medium"
                    >
                      <span>[View]</span>
                      <UIcon name="i-lucide-external-link" class="w-3 h-3" />
                    </a>
                  </li>
                </ul>
                <p v-else class="text-muted">No citations registered.</p>
              </div>

              <!-- Cross Reference Identifiers -->
              <div class="flex flex-col gap-2">
                <h4 class="font-bold text-color text-sm flex items-center gap-1.5">
                  <UIcon name="i-lucide-link" class="w-4 h-4 text-primary-500" />
                  Cross References
                </h4>
                <div v-if="simulator.identifiers.length > 0" class="flex flex-wrap gap-1.5">
                  <a
                    v-for="ident in simulator.identifiers"
                    :key="ident.text"
                    :href="ident.url"
                    target="_blank"
                    rel="noopener noreferrer"
                    class="inline-block"
                  >
                    <UBadge color="neutral" variant="outline" size="md" class="text-xs font-mono font-medium px-2.5 py-1 hover:bg-neutral-100 dark:hover:bg-neutral-800">
                      {{ ident.text }}
                    </UBadge>
                  </a>
                </div>
                <p v-else class="text-muted">No cross references registered.</p>
              </div>

              <!-- Funding -->
              <div class="flex flex-col gap-2">
                <h4 class="font-bold text-color text-sm flex items-center gap-1.5">
                  <UIcon name="i-lucide-banknote" class="w-4 h-4 text-primary-500" />
                  Funding & Grants
                </h4>
                <ul v-if="simulator.funding.length > 0" class="flex flex-col gap-2.5 text-muted leading-relaxed">
                  <li v-for="(f, idx) in simulator.funding" :key="idx" class="p-3 rounded-lg bg-neutral-100/60 dark:bg-neutral-800/60 border border-neutral-200/60 dark:border-neutral-700/60">
                    <a
                      :href="f.funderUrl"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="text-primary-600 hover:underline font-medium"
                    >
                      {{ f.funderName }}
                    </a>
                    <span v-if="f.grant">: {{ f.grant }}</span>
                  </li>
                </ul>
                <p v-else class="text-muted">No funding information registered.</p>
              </div>
            </div>
          </template>
        </UCollapsible>
      </div>

      <!-- Section 5: Raw Specifications JSON (#specifications) -->
      <div id="specifications" class="w-full p-6 border border-neutral-200 dark:border-neutral-800 rounded-xl bg-neutral-50/50 dark:bg-neutral-900/50 shadow-sm">
        <UCollapsible class="w-full flex flex-col gap-2" :default-open="false">
          <UButton
            class="group w-full text-lg font-bold text-color cursor-pointer p-0"
            label="Specifications (JSON)"
            color="neutral"
            variant="link"
            trailing-icon="i-lucide-chevron-down"
            :ui="{ trailingIcon: 'group-data-[state=open]:rotate-180 transition-transform duration-200' }"
            block
          />

          <template #content>
            <div class="mt-4 flex flex-col gap-3">
              <div class="flex items-center justify-end gap-2">
                <UButton
                  size="xs"
                  color="neutral"
                  variant="outline"
                  icon="i-lucide-copy"
                  label="Copy JSON"
                  class="cursor-pointer"
                  @click="copyText(simulator.rawJson, 'Specifications JSON')"
                />
                <UButton
                  size="xs"
                  color="primary"
                  variant="soft"
                  icon="i-lucide-download"
                  label="Download JSON"
                  class="cursor-pointer"
                  @click="downloadSpecificationsJson"
                />
              </div>
              <pre class="p-4 bg-neutral-900 text-neutral-200 rounded-xl overflow-x-auto text-xs font-mono leading-relaxed max-h-96"><code>{{ simulator.rawJson }}</code></pre>
            </div>
          </template>
        </UCollapsible>
      </div>
    </template>
  </section>
</template>
