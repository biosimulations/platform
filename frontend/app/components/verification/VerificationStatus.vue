<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { useClipboard } from '@vueuse/core'
import type { VerifyWorkflowOutput, VerifyWorkflowStatus } from '~/models/verification'

const props = defineProps<{
  workflowId: string
  modelValue?: VerifyWorkflowOutput | null
  autoPoll?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: VerifyWorkflowOutput]
  'completed': [value: VerifyWorkflowOutput]
  'failed': [error: string]
}>()

const config = useRuntimeConfig()
const toast = useToast()
const { copy } = useClipboard()

// Poll every 5s, backing off to at most a minute while requests fail
const POLL_INTERVAL_SECONDS = 5
const MAX_POLL_INTERVAL_SECONDS = 60
// The 4xx answers that can change on retry; ofetch treats the same ones as transient
const TRANSIENT_CLIENT_ERRORS = new Set([408, 409, 425, 429])

// The parent's output only seeds this view when it belongs to the workflow being shown
function matchingModelValue(): VerifyWorkflowOutput | null {
  return props.modelValue?.workflow_id === props.workflowId ? props.modelValue : null
}

const polling = ref(false)
const workflowData = ref<VerifyWorkflowOutput | null>(matchingModelValue())
const errorMsg = ref<string | null>(null)
// Set on a lasting 4xx (no access, unknown or expired ID), which retrying on a timer won't fix
const halted = ref(false)
const pollInterval = ref<number>(POLL_INTERVAL_SECONDS)
const secondsElapsed = ref<number>(0)
const nextRefreshCountdown = ref<number>(POLL_INTERVAL_SECONDS)
let timer: ReturnType<typeof setInterval> | null = null
let inFlight: AbortController | null = null

const status = computed<VerifyWorkflowStatus>(() => {
  return workflowData.value?.workflow_status || 'PENDING'
})

const isDone = computed(() => {
  return status.value === 'COMPLETED' || status.value === 'FAILED' || status.value === 'RUN_ID_NOT_FOUND'
})

const statusBadgeColor = computed(() => {
  switch (status.value) {
    case 'COMPLETED':
      return 'success'
    case 'IN_PROGRESS':
      return 'primary'
    case 'PENDING':
      return 'warning'
    case 'FAILED':
    case 'RUN_ID_NOT_FOUND':
      return 'error'
    default:
      return 'neutral'
  }
})

const statusIcon = computed(() => {
  switch (status.value) {
    case 'COMPLETED':
      return 'i-lucide-check-circle'
    case 'IN_PROGRESS':
      return 'i-svg-spinners:ring-resize'
    case 'PENDING':
      return 'i-lucide-clock'
    case 'FAILED':
    case 'RUN_ID_NOT_FOUND':
      return 'i-lucide-alert-triangle'
    default:
      return 'i-lucide-help-circle'
  }
})

async function fetchStatus() {
  const workflowId = props.workflowId
  if (!workflowId) return
  // Supersede a request still in flight (manual refresh, or a switch to another workflow)
  inFlight?.abort()
  const controller = new AbortController()
  inFlight = controller
  polling.value = true
  errorMsg.value = null

  try {
    const res = await $fetch<VerifyWorkflowOutput>(`${config.public.api_url}/verify/${workflowId}`, {
      params: { _t: Date.now() },
      headers: {
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache'
      },
      signal: controller.signal
    })
    // A late answer for a workflow we've moved away from must not overwrite the current one
    if (controller.signal.aborted || workflowId !== props.workflowId) return
    pollInterval.value = POLL_INTERVAL_SECONDS
    if (res) {
      workflowData.value = res
      emit('update:modelValue', res)

      if (res.workflow_status === 'COMPLETED') {
        stopTimers()
        emit('completed', res)
      } else if (res.workflow_status === 'FAILED' || res.workflow_status === 'RUN_ID_NOT_FOUND') {
        stopTimers()
        const err = res.workflow_error || `Verification failed with status: ${res.workflow_status}`
        errorMsg.value = err
        emit('failed', err)
      }
    }
  } catch (err: any) {
    if (controller.signal.aborted || workflowId !== props.workflowId) return
    const errText = err?.data?.detail || err?.message || 'Failed to poll verification workflow'
    errorMsg.value = errText
    const status: number | undefined = err?.response?.status ?? err?.statusCode
    if (status !== undefined && status >= 400 && status < 500 && !TRANSIENT_CLIENT_ERRORS.has(status)) {
      halted.value = true
      stopTimers()
    } else {
      // Transient, server or network failure: back off rather than hammer a struggling backend
      pollInterval.value = Math.min(pollInterval.value * 2, MAX_POLL_INTERVAL_SECONDS)
    }
  } finally {
    if (inFlight === controller) {
      inFlight = null
      polling.value = false
      // Count down from when the answer arrived, so requests never overlap
      nextRefreshCountdown.value = pollInterval.value
    }
  }
}

function tick() {
  secondsElapsed.value++
  if (props.autoPoll === false || isDone.value || halted.value || polling.value) return
  nextRefreshCountdown.value--
  if (nextRefreshCountdown.value <= 0) {
    fetchStatus()
  }
}

function startTimers() {
  stopTimers()
  secondsElapsed.value = 0
  nextRefreshCountdown.value = pollInterval.value
  timer = setInterval(tick, 1000)
}

function stopTimers() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

// Also the retry after a 4xx halted polling
function manualRefresh() {
  halted.value = false
  pollInterval.value = POLL_INTERVAL_SECONDS
  if (!timer) {
    timer = setInterval(tick, 1000)
  }
  fetchStatus()
}

function copyWorkflowId() {
  copy(props.workflowId)
  toast.add({
    title: 'Workflow ID copied',
    color: 'success',
    icon: 'i-lucide-check'
  })
}

watch(() => props.workflowId, (newId) => {
  inFlight?.abort()
  workflowData.value = matchingModelValue()
  errorMsg.value = null
  halted.value = false
  pollInterval.value = POLL_INTERVAL_SECONDS
  if (newId) {
    startTimers()
    fetchStatus()
  } else {
    stopTimers()
  }
})

watch(() => props.modelValue, (newVal) => {
  // Output for another workflow (a parent that hasn't caught up yet) must not replace this one
  if (!newVal || newVal.workflow_id !== props.workflowId) return
  workflowData.value = newVal
  if (isDone.value) {
    stopTimers()
  } else if (!timer && !halted.value) {
    startTimers()
  }
})

onMounted(() => {
  startTimers()
  fetchStatus()
})

onUnmounted(() => {
  stopTimers()
  inFlight?.abort()
})
</script>

<template>
  <div class="w-full bg-white border border-neutral-200 rounded-xl p-5 shadow-sm">
    <div class="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-4 border-b border-neutral-100">
      <div class="flex items-center gap-3">
        <UBadge
          :color="statusBadgeColor"
          variant="subtle"
          size="lg"
          class="flex items-center gap-1.5 font-medium px-3 py-1"
        >
          <UIcon :name="statusIcon" class="size-4" />
          <span>{{ status }}</span>
        </UBadge>
        <div>
          <h3 class="text-base font-semibold text-neutral-900 flex items-center gap-2">
            Verification Workflow
            <span v-if="!isDone" class="text-xs font-normal text-neutral-500">
              ({{ secondsElapsed }}s elapsed)
            </span>
            <span
              v-if="!isDone && !halted"
              class="inline-flex items-center gap-1 text-[11px] font-normal px-2 py-0.5 rounded-full bg-primary-50 text-primary border border-primary-200"
            >
              <UIcon :name="polling ? 'i-svg-spinners:ring-resize' : 'i-lucide-refresh-cw'" class="size-3" :class="{ 'animate-spin': polling }" />
              <span>{{ polling ? 'Refreshing...' : `Auto-refresh in ${nextRefreshCountdown}s` }}</span>
            </span>
          </h3>
          <p class="text-xs text-neutral-500 flex items-center gap-1.5 mt-0.5">
            ID: <code class="font-mono bg-neutral-100 px-1 py-0.5 rounded text-[11px]">{{ workflowId }}</code>
            <UButton
              icon="i-lucide-copy"
              size="xs"
              variant="ghost"
              color="neutral"
              aria-label="Copy Workflow ID"
              @click="copyWorkflowId"
            />
          </p>
        </div>
      </div>

      <div class="flex items-center gap-2">
        <UButton
          v-if="!isDone"
          icon="i-lucide-refresh-cw"
          size="sm"
          variant="soft"
          color="primary"
          :loading="polling"
          :label="halted ? 'Retry' : 'Refresh Now'"
          @click="manualRefresh"
        />
        <NuxtLink
          v-if="workflowData"
          :to="`?workflow_id=${workflowId}`"
          class="text-xs text-primary hover:underline flex items-center gap-1"
        >
          <UIcon name="i-lucide-external-link" class="size-3" />
          Permalink
        </NuxtLink>
      </div>
    </div>

    <!-- Details Section -->
    <div class="pt-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 text-xs text-neutral-600">
      <div>
        <span class="block text-neutral-400 uppercase tracking-wider text-[10px] font-semibold">Description</span>
        <span class="font-medium text-neutral-800">
          {{ workflowData?.compare_settings?.user_description || 'N/A' }}
        </span>
      </div>
      <div>
        <span class="block text-neutral-400 uppercase tracking-wider text-[10px] font-semibold">Tolerances</span>
        <span class="font-mono text-[11px]">
          rtol: {{ workflowData?.compare_settings?.rel_tol ?? 0.0001 }},
          atol: {{ workflowData?.compare_settings?.abs_tol_min ?? 0.001 }}
        </span>
      </div>
      <div>
        <span class="block text-neutral-400 uppercase tracking-wider text-[10px] font-semibold">Outputs Stored</span>
        <span class="font-medium">
          {{ workflowData?.compare_settings?.include_outputs ? 'Yes (Solutions loaded)' : 'No (Stats only)' }}
        </span>
      </div>
      <div>
        <span class="block text-neutral-400 uppercase tracking-wider text-[10px] font-semibold">Timestamp</span>
        <span>
          {{ workflowData?.timestamp ? new Date(workflowData.timestamp).toLocaleString() : 'N/A' }}
        </span>
      </div>
    </div>

    <!-- Error notice -->
    <UAlert
      v-if="errorMsg || workflowData?.workflow_error"
      color="error"
      variant="subtle"
      icon="i-lucide-alert-circle"
      title="Verification Error"
      :description="errorMsg || workflowData?.workflow_error || ''"
      class="mt-4"
    />
  </div>
</template>
