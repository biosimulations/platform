<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import type { RunData, SimulationRunInfo } from '~/models/verification'
import { useVerificationAnalytics } from '~/composables/useVerificationAnalytics'

const props = withDefaults(defineProps<{
  datasetName: string
  simRunData?: RunData[] | null
  simsRunInfo?: SimulationRunInfo[]
  initialVariable?: string
  initialSimI?: number
  initialSimJ?: number
}>(), {
  initialSimI: 0,
  initialSimJ: 1,
  initialVariable: ''
})

const { extractTrajectory, extractTimePoints } = useVerificationAnalytics()

type ViewMode = 'overlay' | 'tiled' | 'residual'
const viewMode = ref<ViewMode>('overlay')
const yAxisScale = ref<'linear' | 'log'>('linear')
const enableSlider = ref(false)
const residualType = ref<'absolute' | 'relative'>('absolute')

const selectedVar = ref<string>(props.initialVariable || '')
const selectedSimIIdx = ref<number>(props.initialSimI)
const selectedSimJIdx = ref<number>(props.initialSimJ)

// Palette for traces
const colorPalette = [
  '#2563eb', // Blue
  '#dc2626', // Red
  '#16a34a', // Green
  '#9333ea', // Purple
  '#ea580c', // Orange
  '#0891b2', // Cyan
  '#d97706', // Amber
  '#db2777' // Pink
]

// Filter runs matching the active dataset
const currentDatasetRuns = computed(() => {
  if (!props.simRunData) return []
  return props.simRunData.filter(r => r.dataset_name === props.datasetName)
})

// Simulator label mapping
function getSimLabel(runId: string): string {
  if (props.simsRunInfo) {
    const info = props.simsRunInfo.find(s => s.biosim_sim_run.id === runId)
    if (info) {
      const ver = info.biosim_sim_run.simulator_version
      if (ver) {
        return `${ver.name || ver.id} (${ver.version})`
      }
      return info.biosim_sim_run.simulator || info.biosim_sim_run.id
    }
  }
  return runId.slice(0, 8)
}

// Available variables
const availableVariables = computed(() => {
  if (currentDatasetRuns.value.length === 0) return []
  const first = currentDatasetRuns.value[0]
  return first?.var_names?.slice() || []
})

// Auto-select first non-time variable if none selected
watch([availableVariables, () => props.initialVariable], ([vars, initVar]) => {
  if (initVar && vars.includes(initVar)) {
    selectedVar.value = initVar
    return
  }
  if (vars.length > 0 && (!selectedVar.value || !vars.includes(selectedVar.value))) {
    const nonTime = vars.find((v) => {
      const l = v.toLowerCase()
      return l !== 'time' && !l.endsWith('_time') && !l.includes('autogen_time')
    })
    selectedVar.value = nonTime || vars[0]!
  }
}, { immediate: true })

watch(() => props.initialSimI, (val) => {
  if (val !== undefined) selectedSimIIdx.value = val
})

watch(() => props.initialSimJ, (val) => {
  if (val !== undefined) selectedSimJIdx.value = val
})

// Time points
const timeArray = computed<number[]>(() => {
  if (currentDatasetRuns.value.length === 0) return []
  const first = currentDatasetRuns.value[0]!
  return extractTimePoints(first) || []
})

// Base layout generator
function getBaseLayout(title: string, yTitle = 'Value') {
  return {
    title: {
      text: title,
      font: { size: 14, color: '#334155' }
    },
    autosize: true,
    margin: { l: 55, r: 25, t: 45, b: 45 },
    xaxis: {
      title: 'Time',
      gridcolor: '#f1f5f9',
      zerolinecolor: '#cbd5e1',
      rangeslider: enableSlider.value ? { visible: true, thickness: 0.12 } : { visible: false }
    },
    yaxis: {
      title: yTitle,
      type: yAxisScale.value,
      gridcolor: '#f1f5f9',
      zerolinecolor: '#cbd5e1'
    },
    legend: {
      orientation: 'h',
      x: 0,
      y: 1.15,
      font: { size: 11 }
    },
    plot_bgcolor: 'transparent',
    paper_bgcolor: 'transparent'
  }
}

// 1. Overlay Plot Data & Layout
const overlayData = computed(() => {
  if (!selectedVar.value || currentDatasetRuns.value.length === 0) return []

  return currentDatasetRuns.value.map((run, idx) => {
    const yVals = extractTrajectory(run, selectedVar.value) || []
    const xVals = extractTimePoints(run) || timeArray.value
    const color = colorPalette[idx % colorPalette.length]

    return {
      x: xVals,
      y: yVals,
      type: 'scatter',
      mode: 'lines',
      name: getSimLabel(run.run_id),
      line: {
        color,
        width: 2,
        dash: idx % 2 === 1 ? 'dot' : 'solid'
      }
    }
  })
})

const overlayLayout = computed(() => {
  return getBaseLayout(`${selectedVar.value} — Multi-Simulator Overlay`)
})

// 2. Tiled Subplots Data
interface TiledCardData {
  runId: string
  label: string
  color: string
  data: any[]
  layout: any
  minVal: number | null
  maxVal: number | null
  finalVal: number | null
}

const tiledCards = computed<TiledCardData[]>(() => {
  if (!selectedVar.value || currentDatasetRuns.value.length === 0) return []

  return currentDatasetRuns.value.map((run, idx) => {
    const yVals = extractTrajectory(run, selectedVar.value) || []
    const xVals = extractTimePoints(run) || timeArray.value
    const color = colorPalette[idx % colorPalette.length]!
    const label = getSimLabel(run.run_id)

    const validY = yVals.filter(v => !isNaN(v) && isFinite(v))
    const minVal = validY.length > 0 ? Math.min(...validY) : null
    const maxVal = validY.length > 0 ? Math.max(...validY) : null
    const finalVal = validY.length > 0 ? validY[validY.length - 1]! : null

    const data = [{
      x: xVals,
      y: yVals,
      type: 'scatter',
      mode: 'lines',
      name: label,
      line: { color, width: 2 }
    }]

    const layout = {
      title: { text: label, font: { size: 12, color: '#334155' } },
      autosize: true,
      margin: { l: 45, r: 15, t: 35, b: 35 },
      xaxis: { title: 'Time', gridcolor: '#f1f5f9' },
      yaxis: { type: yAxisScale.value, gridcolor: '#f1f5f9' },
      plot_bgcolor: 'transparent',
      paper_bgcolor: 'transparent',
      showlegend: false
    }

    return {
      runId: run.run_id,
      label,
      color,
      data,
      layout,
      minVal,
      maxVal,
      finalVal
    }
  })
})

// 3. Residual / Difference Data
const residualSimOptions = computed(() => {
  return currentDatasetRuns.value.map((r, idx) => ({
    label: getSimLabel(r.run_id),
    value: idx
  }))
})

const residualStats = computed(() => {
  const runA = currentDatasetRuns.value[selectedSimIIdx.value]
  const runB = currentDatasetRuns.value[selectedSimJIdx.value]
  if (!runA || !runB || !selectedVar.value) {
    return { diffArray: [], timeVals: [], maxDiff: 0, meanDiff: 0 }
  }

  const y1 = extractTrajectory(runA, selectedVar.value) || []
  const y2 = extractTrajectory(runB, selectedVar.value) || []
  const timeVals = extractTimePoints(runA) || timeArray.value
  const len = Math.min(y1.length, y2.length)

  const diffArray: number[] = []
  let sumDiff = 0
  let maxDiff = 0
  let validPoints = 0

  for (let t = 0; t < len; t++) {
    const v1 = y1[t]!
    const v2 = y2[t]!
    if (isNaN(v1) || isNaN(v2)) {
      diffArray.push(NaN)
      continue
    }

    let val = 0
    if (residualType.value === 'absolute') {
      val = v1 - v2
    } else {
      const denom = Math.max(Math.abs(v2), 1e-9)
      val = (v1 - v2) / denom
    }

    diffArray.push(val)
    const absVal = Math.abs(val)
    if (absVal > maxDiff) maxDiff = absVal
    sumDiff += absVal
    validPoints++
  }

  const meanDiff = validPoints > 0 ? sumDiff / validPoints : 0
  return { diffArray, timeVals: timeVals.slice(0, len), maxDiff, meanDiff }
})

const residualData = computed(() => {
  const { diffArray, timeVals } = residualStats.value
  const labelA = currentDatasetRuns.value[selectedSimIIdx.value] ? getSimLabel(currentDatasetRuns.value[selectedSimIIdx.value]!.run_id) : 'Sim A'
  const labelB = currentDatasetRuns.value[selectedSimJIdx.value] ? getSimLabel(currentDatasetRuns.value[selectedSimJIdx.value]!.run_id) : 'Sim B'

  return [
    {
      x: timeVals,
      y: diffArray,
      type: 'scatter',
      mode: 'lines',
      name: `${labelA} − ${labelB}`,
      line: { color: '#dc2626', width: 2 }
    }
  ]
})

const residualLayout = computed(() => {
  const yTitle = residualType.value === 'absolute' ? '&Delta; (y1 - y2)' : 'Relative Error (y1 - y2) / |y2|'
  const layout = getBaseLayout(`${selectedVar.value} — Residual / Discrepancy Curve`, yTitle)
  // Add a horizontal zero line
  return {
    ...layout,
    shapes: [
      {
        type: 'line',
        xref: 'paper',
        x0: 0,
        x1: 1,
        y0: 0,
        y1: 0,
        line: { color: '#64748b', width: 1, dash: 'dash' }
      }
    ]
  }
})
</script>

<template>
  <div class="w-full bg-white border border-neutral-200 rounded-xl p-5 shadow-sm">
    <!-- Header with Variable Selection & View Controls -->
    <div class="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-4 pb-4 mb-4 border-b border-neutral-100">
      <div>
        <h3 class="text-base font-semibold text-neutral-900 flex items-center gap-2">
          <UIcon name="i-lucide-activity" class="size-4 text-primary" />
          Multi-Solution Trajectory Viewer
        </h3>
        <p class="text-xs text-neutral-500 mt-0.5">
          Inspect, overlay, and compute residuals across simulator solutions for dataset <code class="font-mono text-[11px] bg-neutral-100 px-1 py-0.5 rounded">{{ datasetName }}</code>.
        </p>
      </div>

      <!-- Controls: Variable selector, View mode tabs, Scale toggle -->
      <div class="flex items-center gap-2 flex-wrap">
        <!-- Variable Dropdown -->
        <div class="w-48">
          <USelect
            v-model="selectedVar"
            :items="availableVariables"
            placeholder="Select variable"
            size="sm"
            class="w-full font-mono text-xs"
          />
        </div>

        <!-- View Mode Buttons -->
        <div class="flex items-center rounded-lg border border-neutral-200 p-0.5 bg-neutral-50">
          <UButton
            size="xs"
            :variant="viewMode === 'overlay' ? 'solid' : 'ghost'"
            :color="viewMode === 'overlay' ? 'primary' : 'neutral'"
            label="Overlay"
            icon="i-lucide-layers"
            @click="viewMode = 'overlay'"
          />
          <UButton
            size="xs"
            :variant="viewMode === 'tiled' ? 'solid' : 'ghost'"
            :color="viewMode === 'tiled' ? 'primary' : 'neutral'"
            label="Tiled"
            icon="i-lucide-layout-grid"
            @click="viewMode = 'tiled'"
          />
          <UButton
            size="xs"
            :variant="viewMode === 'residual' ? 'solid' : 'ghost'"
            :color="viewMode === 'residual' ? 'primary' : 'neutral'"
            label="Residual"
            icon="i-lucide-split"
            @click="viewMode = 'residual'"
          />
        </div>

        <!-- Y-scale Linear/Log -->
        <UButton
          size="xs"
          variant="outline"
          color="neutral"
          :icon="yAxisScale === 'log' ? 'i-lucide-binary' : 'i-lucide-trending-up'"
          :label="yAxisScale === 'log' ? 'Log Y' : 'Linear Y'"
          @click="yAxisScale = yAxisScale === 'linear' ? 'log' : 'linear'"
        />

        <!-- Range Slider Toggle -->
        <UButton
          size="xs"
          variant="outline"
          color="neutral"
          :icon="enableSlider ? 'i-lucide-chart-line' : 'i-lucide-chevrons-left-right-ellipsis'"
          :label="enableSlider ? 'Hide Slider' : 'Show Slider'"
          @click="enableSlider = !enableSlider"
        />
      </div>
    </div>

    <!-- Empty State if no sim_run_data -->
    <UEmpty
      v-if="!simRunData || simRunData.length === 0"
      icon="i-lucide-bar-chart-2"
      title="Trajectory solutions not stored"
      description="This verification job was executed with include_outputs: false. Re-run verification with 'Store simulation solutions' enabled to visualize and compare trajectories interactively."
      variant="outline"
    />

    <!-- View Mode 1: Overlay -->
    <div v-else-if="viewMode === 'overlay'" class="w-full">
      <div class="h-[440px] w-full border border-neutral-100 rounded-lg p-2 bg-neutral-50/50">
        <ClientOnly>
          <PlotlyChart
            :data="overlayData"
            :layout="overlayLayout"
            class="w-full h-full"
          />
        </ClientOnly>
      </div>
    </div>

    <!-- View Mode 2: Tiled Subplots -->
    <div v-else-if="viewMode === 'tiled'" class="w-full">
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <div
          v-for="card in tiledCards"
          :key="card.runId"
          class="border border-neutral-200 rounded-lg p-3 bg-white shadow-xs flex flex-col"
        >
          <div class="flex items-center justify-between pb-2 mb-2 border-b border-neutral-100">
            <span class="text-xs font-semibold text-neutral-800 truncate" :title="card.label">
              {{ card.label }}
            </span>
            <span class="size-2.5 rounded-full shrink-0" :style="{ backgroundColor: card.color }" />
          </div>

          <div class="h-56 w-full">
            <ClientOnly>
              <PlotlyChart
                :data="card.data"
                :layout="card.layout"
                class="w-full h-full"
              />
            </ClientOnly>
          </div>

          <!-- Quick Metrics -->
          <div class="mt-2 pt-2 border-t border-neutral-100 grid grid-cols-3 text-[10px] text-neutral-500 font-mono">
            <div>Min: <span class="font-semibold text-neutral-700">{{ card.minVal !== null ? card.minVal.toFixed(3) : '—' }}</span></div>
            <div>Max: <span class="font-semibold text-neutral-700">{{ card.maxVal !== null ? card.maxVal.toFixed(3) : '—' }}</span></div>
            <div>End: <span class="font-semibold text-neutral-700">{{ card.finalVal !== null ? card.finalVal.toFixed(3) : '—' }}</span></div>
          </div>
        </div>
      </div>
    </div>

    <!-- View Mode 3: Residual / Difference View -->
    <div v-else-if="viewMode === 'residual'" class="w-full">
      <!-- Pair Selector & Difference Type -->
      <div class="flex flex-wrap items-center justify-between gap-3 mb-4 p-3 bg-neutral-50 rounded-lg text-xs">
        <div class="flex items-center gap-2 flex-wrap">
          <span class="text-neutral-500 font-medium">Compare Pair:</span>
          <div class="w-44">
            <USelect
              v-model="selectedSimIIdx"
              :items="residualSimOptions"
              value-key="value"
              size="xs"
              class="w-full"
            />
          </div>
          <span class="text-neutral-400 font-bold">&minus;</span>
          <div class="w-44">
            <USelect
              v-model="selectedSimJIdx"
              :items="residualSimOptions"
              value-key="value"
              size="xs"
              class="w-full"
            />
          </div>
        </div>

        <div class="flex items-center gap-2">
          <span class="text-neutral-500">Metric:</span>
          <UButton
            size="xs"
            :variant="residualType === 'absolute' ? 'solid' : 'ghost'"
            :color="residualType === 'absolute' ? 'primary' : 'neutral'"
            label="Absolute (&Delta;)"
            @click="residualType = 'absolute'"
          />
          <UButton
            size="xs"
            :variant="residualType === 'relative' ? 'solid' : 'ghost'"
            :color="residualType === 'relative' ? 'primary' : 'neutral'"
            label="Relative Error"
            @click="residualType = 'relative'"
          />
        </div>
      </div>

      <div class="h-[400px] w-full border border-neutral-100 rounded-lg p-2 bg-neutral-50/50">
        <ClientOnly>
          <PlotlyChart
            :data="residualData"
            :layout="residualLayout"
            class="w-full h-full"
          />
        </ClientOnly>
      </div>

      <!-- Difference Stats Summary -->
      <div class="mt-3 p-3 bg-neutral-50 rounded-lg flex items-center justify-around text-xs">
        <div>
          <span class="text-neutral-400 mr-1.5">Max Discrepancy (&Delta;<sub>max</sub>):</span>
          <span class="font-mono font-bold text-neutral-800">
            {{ residualStats.maxDiff.toExponential(4) }}
          </span>
        </div>
        <div>
          <span class="text-neutral-400 mr-1.5">Mean Discrepancy:</span>
          <span class="font-mono font-bold text-neutral-800">
            {{ residualStats.meanDiff.toExponential(4) }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>
