<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ComparisonStatistics } from '~/models/verification'
import { useVerificationAnalytics } from '~/composables/useVerificationAnalytics'

export interface SimulatorInfo {
  id: string
  name: string
  version?: string
}

export interface ExcludedSimulatorInfo {
  id: string
  name: string
  version?: string
  status?: string
  reason?: string
  runId?: string
  logsUrl?: string
}

const props = withDefaults(defineProps<{
  simulators: SimulatorInfo[]
  excludedSimulators?: ExcludedSimulatorInfo[]
  matrix: ComparisonStatistics[][]
  selectedI?: number
  selectedJ?: number
}>(), {
  excludedSimulators: () => [],
  selectedI: 0,
  selectedJ: 1
})

const emit = defineEmits<{
  'selectPair': [i: number, j: number]
}>()

const { computePairConcordance } = useVerificationAnalytics()

function formatSimLabel(sim: SimulatorInfo): string {
  if (sim.name && sim.name !== 'unknown') {
    if (sim.version) {
      return `${sim.name} (${sim.version})`
    }
    return sim.name
  }
  return sim.id ? `Run ${sim.id.slice(0, 8)}…` : 'Unknown'
}

interface CellData {
  i: number
  j: number
  simI: SimulatorInfo
  simJ: SimulatorInfo
  stats?: ComparisonStatistics
  percentage: number
  concordantCount: number
  totalCount: number
  isError: boolean
  isExcluded: boolean
  errorMessage?: string
  colorClass: string
  isSelected: boolean
  excludedI?: ExcludedSimulatorInfo
  excludedJ?: ExcludedSimulatorInfo
}

const numValid = computed(() => props.simulators.length)
const allSimulators = computed<SimulatorInfo[]>(() => [
  ...props.simulators,
  ...props.excludedSimulators
])
const n = computed(() => allSimulators.value.length)

// Equal sizing distribution across all columns
const rowHeaderWidthPercent = computed(() => {
  if (n.value <= 2) return 24
  if (n.value <= 4) return 20
  return 18
})

const simColWidthPercent = computed(() => {
  if (n.value === 0) return 0
  return (100 - rowHeaderWidthPercent.value) / n.value
})

const gridCells = computed<CellData[][]>(() => {
  const result: CellData[][] = []

  for (let i = 0; i < n.value; i++) {
    const row: CellData[] = []
    const simI = allSimulators.value[i]!
    const isIExcluded = i >= numValid.value

    for (let j = 0; j < n.value; j++) {
      const simJ = allSimulators.value[j]!
      const isJExcluded = j >= numValid.value
      const isExcluded = isIExcluded || isJExcluded

      if (isExcluded) {
        const excI = isIExcluded ? props.excludedSimulators[i - numValid.value] : undefined
        const excJ = isJExcluded ? props.excludedSimulators[j - numValid.value] : undefined
        const reason = (excI?.reason)
          || (excJ?.reason)
          || 'Run failed; cannot compare'

        row.push({
          i,
          j,
          simI,
          simJ,
          percentage: 0,
          concordantCount: 0,
          totalCount: 0,
          isError: false,
          isExcluded: true,
          errorMessage: reason,
          colorClass: 'bg-amber-100/90 text-amber-900 border border-amber-300 shadow-2xs',
          isSelected: false,
          excludedI: excI,
          excludedJ: excJ
        })
        continue
      }

      const stats = props.matrix?.[i]?.[j]
      const concordance = computePairConcordance(stats)

      const isSelected = (props.selectedI === i && props.selectedJ === j)
        || (props.selectedI === j && props.selectedJ === i)

      let colorClass = 'bg-neutral-100 text-neutral-500'
      if (concordance.isError) {
        colorClass = 'bg-rose-100 text-rose-800 border border-rose-300'
      } else if (concordance.percentage === 100) {
        colorClass = 'bg-emerald-500 hover:bg-emerald-600 text-white font-semibold'
      } else if (concordance.percentage >= 70) {
        colorClass = 'bg-amber-400 hover:bg-amber-500 text-neutral-900 font-semibold'
      } else {
        colorClass = 'bg-rose-500 hover:bg-rose-600 text-white font-semibold'
      }

      row.push({
        i,
        j,
        simI,
        simJ,
        stats,
        percentage: concordance.percentage,
        concordantCount: concordance.concordantCount,
        totalCount: concordance.totalCount,
        isError: concordance.isError,
        isExcluded: false,
        errorMessage: concordance.errorMessage,
        colorClass,
        isSelected
      })
    }
    result.push(row)
  }

  return result
})

// Error manifest modal state
const isErrorModalOpen = ref(false)
const isErrorModalFullscreen = ref(false)

watch(isErrorModalOpen, (open) => {
  if (!open) {
    isErrorModalFullscreen.value = false
  }
})
const selectedErrorData = ref<{
  title: string
  description: string
  simI: SimulatorInfo
  simJ: SimulatorInfo
  isExcluded: boolean
  isError: boolean
  errorMessage?: string
  failedSimulator?: ExcludedSimulatorInfo
} | null>(null)

function handleCellClick(cell: CellData) {
  if (cell.isExcluded) {
    const failedSim = cell.excludedI || cell.excludedJ || props.excludedSimulators[0]
    selectedErrorData.value = {
      title: 'Simulation Run Failed',
      description: 'The simulation job could not complete successfully on the BioSimulations execution cluster.',
      simI: cell.simI,
      simJ: cell.simJ,
      isExcluded: true,
      isError: false,
      errorMessage: cell.errorMessage || failedSim?.reason || 'Simulation run failed or produced no HDF5 output data.',
      failedSimulator: failedSim
    }
    isErrorModalOpen.value = true
    return
  }

  if (cell.isError) {
    selectedErrorData.value = {
      title: 'Dataset Comparison Error',
      description: 'Both simulation runs finished, but the results could not be numerically compared.',
      simI: cell.simI,
      simJ: cell.simJ,
      isExcluded: false,
      isError: true,
      errorMessage: cell.errorMessage || 'Variables or dataset shapes do not match between these solvers.',
      failedSimulator: undefined
    }
    isErrorModalOpen.value = true
    return
  }

  emit('selectPair', cell.i, cell.j)
}

function openExcludedHeaderDetails(excludedIndex: number) {
  const exc = props.excludedSimulators[excludedIndex]
  if (!exc) return
  selectedErrorData.value = {
    title: 'Simulator Execution Error',
    description: `Diagnostic details for simulator ${exc.name}${exc.version ? ` (${exc.version})` : ''}.`,
    simI: { id: exc.id, name: exc.name, version: exc.version },
    simJ: { id: exc.id, name: exc.name, version: exc.version },
    isExcluded: true,
    isError: false,
    errorMessage: exc.reason || `Simulation status: ${exc.status || 'FAILED'}`,
    failedSimulator: exc
  }
  isErrorModalOpen.value = true
}
</script>

<template>
  <div class="w-full bg-white border border-neutral-200 rounded-xl p-5 sm:p-6 shadow-sm">
    <div class="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 pb-4 mb-4 border-b border-neutral-100">
      <div>
        <h3 class="text-base sm:text-lg font-semibold text-neutral-900 flex items-center gap-2">
          <UIcon name="i-lucide-grid" class="size-5 text-primary shrink-0" />
          Concordance Heatmap Matrix
        </h3>
        <p class="text-xs text-neutral-500 mt-0.5">
          Pairwise agreement rate across observable trajectories. Click any valid cell to inspect detailed variables and plots.
        </p>
      </div>

      <!-- Legend -->
      <div class="flex items-center gap-3 text-xs flex-wrap">
        <div class="flex items-center gap-1.5">
          <span class="size-3 rounded bg-emerald-500 shrink-0" />
          <span class="text-neutral-600">100%</span>
        </div>
        <div class="flex items-center gap-1.5">
          <span class="size-3 rounded bg-amber-400 shrink-0" />
          <span class="text-neutral-600">70%–99%</span>
        </div>
        <div class="flex items-center gap-1.5">
          <span class="size-3 rounded bg-rose-500 shrink-0" />
          <span class="text-neutral-600">&lt;70%</span>
        </div>
        <div class="flex items-center gap-1.5">
          <span class="size-3 rounded bg-amber-100 border border-amber-300 shrink-0" />
          <span class="text-neutral-600">Run failed / Excluded</span>
        </div>
        <div class="flex items-center gap-1.5">
          <span class="size-3 rounded bg-neutral-300 shrink-0" />
          <span class="text-neutral-600">Error / Mismatch</span>
        </div>
      </div>
    </div>

    <!-- Matrix Table with equal column distribution -->
    <div class="overflow-x-auto">
      <table class="w-full table-fixed border-collapse min-w-[550px]">
        <colgroup>
          <col :style="{ width: `${rowHeaderWidthPercent}%` }">
          <col
            v-for="idx in n"
            :key="idx"
            :style="{ width: `${simColWidthPercent}%` }"
          >
        </colgroup>
        <thead>
          <tr>
            <th scope="col" class="py-2.5 px-3 text-left text-xs font-semibold text-neutral-400 uppercase tracking-wider">
              Simulator
            </th>
            <th
              v-for="(sim, idx) in allSimulators"
              :key="idx"
              scope="col"
              class="py-2.5 px-2 text-center text-xs font-medium text-neutral-700"
            >
              <div
                class="truncate w-full px-1 flex flex-col items-center gap-0.5"
                :class="idx >= numValid ? 'cursor-pointer hover:opacity-80 transition-opacity' : ''"
                :title="idx >= numValid ? `${formatSimLabel(sim)} failed. Click to reveal issue.` : formatSimLabel(sim)"
                @click="idx >= numValid && openExcludedHeaderDetails(idx - numValid)"
              >
                <span class="truncate max-w-full">{{ formatSimLabel(sim) }}</span>
                <UBadge
                  v-if="idx >= numValid"
                  size="xs"
                  color="warning"
                  variant="subtle"
                  class="text-[9px] py-0 px-1 leading-tight"
                >
                  Failed
                </UBadge>
              </div>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, i) in gridCells" :key="i">
            <td class="py-2.5 px-3 text-xs font-medium text-neutral-800 border-r border-neutral-100">
              <div
                class="truncate w-full pr-2 flex items-center justify-between gap-1"
                :class="i >= numValid ? 'cursor-pointer hover:opacity-80 transition-opacity' : ''"
                :title="i >= numValid ? `${formatSimLabel(allSimulators[i]!)} failed. Click to reveal issue.` : formatSimLabel(allSimulators[i]!)"
                @click="i >= numValid && openExcludedHeaderDetails(i - numValid)"
              >
                <span class="truncate">{{ formatSimLabel(allSimulators[i]!) }}</span>
                <UBadge
                  v-if="i >= numValid"
                  size="xs"
                  color="warning"
                  variant="subtle"
                  class="text-[9px] py-0 px-1 shrink-0 leading-tight"
                >
                  Failed
                </UBadge>
              </div>
            </td>
            <td
              v-for="cell in row"
              :key="cell.j"
              class="p-1.5 text-center"
            >
              <button
                type="button"
                :class="[
                  'w-full py-3 px-1.5 rounded-lg text-xs transition-all duration-150 flex flex-col items-center justify-center shadow-xs cursor-pointer',
                  cell.colorClass,
                  cell.isExcluded
                    ? 'hover:ring-2 hover:ring-amber-400 hover:scale-[1.02]'
                    : (cell.isError
                      ? 'hover:ring-2 hover:ring-rose-400 hover:scale-[1.02]'
                      : (cell.isSelected
                        ? 'ring-2 ring-primary ring-offset-2 scale-95 font-bold shadow-md'
                        : 'hover:scale-[1.02]'))
                ]"
                :title="cell.isExcluded ? `${formatSimLabel(cell.simI)} vs ${formatSimLabel(cell.simJ)}: Run failed. Click to reveal issue manifest.` : (cell.isError ? `Comparison issue: ${cell.errorMessage}. Click to reveal issue manifest.` : `${formatSimLabel(cell.simI)} vs ${formatSimLabel(cell.simJ)}: ${cell.concordantCount}/${cell.totalCount} concordant (${cell.percentage}%)`)"
                @click="handleCellClick(cell)"
              >
                <template v-if="cell.isExcluded">
                  <UIcon name="i-lucide-alert-triangle" class="size-4 text-amber-600 mb-0.5 shrink-0" />
                  <span class="text-xs font-semibold leading-tight text-center px-1">
                    Run failed
                  </span>
                  <span class="text-[9px] text-amber-700 leading-tight mt-0.5 underline decoration-dotted">
                    Click for issue
                  </span>
                </template>
                <template v-else-if="cell.isError">
                  <UIcon name="i-lucide-alert-circle" class="size-3.5 text-rose-600 mb-0.5 shrink-0" />
                  <span class="text-[11px] leading-tight font-semibold">
                    Mismatch
                  </span>
                  <span class="text-[9px] opacity-80 mt-0.5 underline decoration-dotted">
                    Click for issue
                  </span>
                </template>
                <template v-else>
                  <span class="text-sm font-semibold leading-tight">
                    {{ cell.percentage }}%
                  </span>
                  <span v-if="cell.totalCount > 0" class="text-[10px] opacity-80 mt-0.5">
                    {{ cell.concordantCount }}/{{ cell.totalCount }}
                  </span>
                </template>
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="mt-3 text-right text-[11px] text-neutral-400">
      Active inspection pair:
      <span class="font-semibold text-neutral-700">
        {{ simulators[selectedI]?.name || 'Sim A' }} vs {{ simulators[selectedJ]?.name || 'Sim B' }}
      </span>
    </div>

    <!-- Simulator & Comparison Issue Manifest Modal -->
    <UModal
      v-model:open="isErrorModalOpen"
      :fullscreen="isErrorModalFullscreen"
      data-lenis-prevent
      :ui="{
        content: isErrorModalFullscreen
          ? 'w-screen h-dvh max-w-none max-h-none rounded-none flex flex-col lenis-prevent'
          : 'max-w-lg flex flex-col lenis-prevent',
        body: 'flex-1 overflow-y-auto min-h-0 overscroll-contain p-4 lenis-prevent'
      }"
    >
      <template #header>
        <div class="flex items-center justify-between w-full">
          <div>
            <h3 class="text-base font-semibold text-neutral-900">
              {{ selectedErrorData?.title || 'Simulator Issue Manifest' }}
            </h3>
            <p class="text-xs text-neutral-500 mt-0.5">
              {{ selectedErrorData?.description || 'Diagnostic details for this simulation or comparison issue.' }}
            </p>
          </div>
          <div class="flex items-center gap-1 shrink-0">
            <UButton
              size="sm"
              variant="ghost"
              color="neutral"
              :icon="isErrorModalFullscreen ? 'i-lucide-minimize-2' : 'i-lucide-maximize-2'"
              :title="isErrorModalFullscreen ? 'Exit Fullscreen' : 'Enter Fullscreen'"
              @click="isErrorModalFullscreen = !isErrorModalFullscreen"
            />
            <UButton
              size="sm"
              variant="ghost"
              color="neutral"
              icon="i-lucide-x"
              title="Close"
              @click="isErrorModalOpen = false"
            />
          </div>
        </div>
      </template>
      <template #body>
        <div v-if="selectedErrorData" class="flex flex-col gap-4 p-4 text-xs">
          <!-- Solvers involved -->
          <div class="grid grid-cols-2 gap-3 p-3 bg-neutral-50 rounded-lg">
            <div>
              <span class="text-neutral-400 block text-[10px] uppercase font-semibold">Simulator A</span>
              <span class="font-semibold text-neutral-800">
                {{ formatSimLabel(selectedErrorData.simI) }}
              </span>
            </div>
            <div>
              <span class="text-neutral-400 block text-[10px] uppercase font-semibold">Simulator B</span>
              <span class="font-semibold text-neutral-800">
                {{ formatSimLabel(selectedErrorData.simJ) }}
              </span>
            </div>
          </div>

          <!-- Failed Simulator Details (if excluded) -->
          <div v-if="selectedErrorData.failedSimulator" class="space-y-2">
            <div class="flex items-center justify-between">
              <span class="text-neutral-500 font-medium">Failure Status:</span>
              <UBadge color="error" variant="subtle" size="xs">
                {{ selectedErrorData.failedSimulator.status || 'FAILED' }}
              </UBadge>
            </div>
            <div v-if="selectedErrorData.failedSimulator.runId" class="flex items-center justify-between">
              <span class="text-neutral-500 font-medium">BioSimulations Run ID:</span>
              <a
                :href="`https://biosimulations.org/runs/${selectedErrorData.failedSimulator.runId}`"
                target="_blank"
                rel="noopener noreferrer"
                class="font-mono text-primary hover:underline flex items-center gap-1"
              >
                {{ selectedErrorData.failedSimulator.runId }}
                <UIcon name="i-lucide-external-link" class="size-3" />
              </a>
            </div>
          </div>

          <!-- Diagnostic / Error Message Box -->
          <div class="space-y-1">
            <span class="text-neutral-500 font-medium block">Issue Manifest:</span>
            <div class="p-3 bg-red-50 border border-red-200 rounded-lg font-mono text-[11px] text-red-800 whitespace-pre-wrap break-words leading-relaxed max-h-48 overflow-y-auto">
              {{ selectedErrorData.errorMessage || 'No detailed error message was returned.' }}
            </div>
          </div>

          <!-- Guidance / Troubleshooting Info -->
          <div class="p-3 bg-amber-50 border border-amber-200 rounded-lg text-amber-800 flex items-start gap-2">
            <UIcon name="i-lucide-info" class="size-4 shrink-0 mt-0.5 text-amber-600" />
            <div class="space-y-0.5">
              <p class="font-semibold text-[11px]">Why did this occur?</p>
              <p class="text-[11px] leading-relaxed text-amber-700">
                <span v-if="selectedErrorData.isExcluded">
                  This simulator was matched based on KiSAO algorithm and model format capabilities, but the simulator container failed during execution on the BioSimulations cluster (e.g. unsupported model features, events, or solver timeout).
                </span>
                <span v-else>
                  The simulations succeeded, but the observable variables or output array shapes could not be directly aligned for numerical tolerance comparison.
                </span>
              </p>
            </div>
          </div>
        </div>
      </template>
      <template #footer>
        <div class="flex justify-between items-center w-full p-4 pt-0">
          <div v-if="selectedErrorData?.failedSimulator?.runId">
            <UButton
              size="xs"
              variant="outline"
              color="neutral"
              icon="i-lucide-terminal"
              label="View BioSimulations Logs"
              :to="`https://api.biosimulations.org/logs/${selectedErrorData.failedSimulator.runId}`"
              target="_blank"
            />
          </div>
          <div class="ml-auto">
            <UButton label="Close" color="neutral" variant="ghost" size="xs" @click="isErrorModalOpen = false" />
          </div>
        </div>
      </template>
    </UModal>
  </div>
</template>
