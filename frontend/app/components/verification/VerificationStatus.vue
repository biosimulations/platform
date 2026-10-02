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

const polling = ref(false)
const workflowData = ref<VerifyWorkflowOutput | null>(props.modelValue || null)
const errorMsg = ref<string | null>(null)
const secondsElapsed = ref<number>(0)
const nextRefreshCountdown = ref<number>(5)
let timer: ReturnType<typeof setInterval> | null = null

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
  if (!props.workflowId) return
  polling.value = true
  errorMsg.value = null

  try {
    const res = await $fetch<VerifyWorkflowOutput>(`${config.public.api_url}/verify/${props.workflowId}`, {
      params: { _t: Date.now() },
      headers: {
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache'
      }
    })
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
    const errText = err?.data?.detail || err?.message || 'Failed to poll verification workflow'
    errorMsg.value = errText
  } finally {
    polling.value = false
  }
}

function startTimers() {
  stopTimers()
  secondsElapsed.value = 0
  nextRefreshCountdown.value = 5

  timer = setInterval(() => {
    secondsElapsed.value++

    if (props.autoPoll !== false && !isDone.value) {
      nextRefreshCountdown.value--
      if (nextRefreshCountdown.value <= 0) {
        nextRefreshCountdown.value = 5
        fetchStatus()
      }
    }
  }, 1000)
}

function stopTimers() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

function manualRefresh() {
  nextRefreshCountdown.value = 5
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
  if (newId) {
    workflowData.value = props.modelValue || null
    startTimers()
    fetchStatus()
  }
})

watch(() => props.modelValue, (newVal) => {
  if (newVal) {
    workflowData.value = newVal
    if (!isDone.value && !timer) {
      startTimers()
    } else if (isDone.value) {
      stopTimers()
    }
  }
}, { deep: true })

onMounted(() => {
  startTimers()
  fetchStatus()
})

onUnmounted(() => {
  stopTimers()
})
</script>

<template>
  <div class="w-full bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-xl p-5 shadow-sm">
    <div class="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-4 border-b border-neutral-100 dark:border-neutral-800">
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
          <h3 class="text-base font-semibold text-neutral-900 dark:text-white flex items-center gap-2">
            Verification Workflow
            <span v-if="!isDone" class="text-xs font-normal text-neutral-500">
              ({{ secondsElapsed }}s elapsed)
            </span>
            <span
              v-if="!isDone"
              class="inline-flex items-center gap-1 text-[11px] font-normal px-2 py-0.5 rounded-full bg-primary-50 dark:bg-primary-950/40 text-primary border border-primary-200 dark:border-primary-800"
            >
              <UIcon :name="polling ? 'i-svg-spinners:ring-resize' : 'i-lucide-refresh-cw'" class="size-3" :class="{ 'animate-spin': polling }" />
              <span>{{ polling ? 'Refreshing...' : `Auto-refresh in ${nextRefreshCountdown}s` }}</span>
            </span>
          </h3>
          <p class="text-xs text-neutral-500 flex items-center gap-1.5 mt-0.5">
            ID: <code class="font-mono bg-neutral-100 dark:bg-neutral-800 px-1 py-0.5 rounded text-[11px]">{{ workflowId }}</code>
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
          label="Refresh Now"
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
    <div class="pt-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 text-xs text-neutral-600 dark:text-neutral-400">
      <div>
        <span class="block text-neutral-400 uppercase tracking-wider text-[10px] font-semibold">Description</span>
        <span class="font-medium text-neutral-800 dark:text-neutral-200">
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
