<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { useClipboard } from '@vueuse/core'
import { verificationIsDone, verificationDisplayStatus, isUnavailableVerification } from '~/utils/verification-status'
import type { VerificationRecovery, VerifyWorkflowOutput } from '~/models/verification'

const props = withDefaults(defineProps<{
  workflowId: string
  modelValue?: VerifyWorkflowOutput | null
  autoPoll?: boolean
}>(), { autoPoll: true })

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

const unavailable = ref(false)
const recovery = ref<VerificationRecovery | null>(null)
const reverifyConfirmed = ref(false)
const reverifyDraft = ref({
  simulators: 'amici, copasi, pysces, tellurium, vcell',
  include_outputs: false,
  user_description: 'reverification',
  rel_tol: 0.0001,
  abs_tol_min: 0.001,
  abs_tol_scale: 0.00001,
  cache_buster: '0',
})
const linkedVerificationId = ref<string | null>(null)
const recoveryError = ref<string | null>(null)
const recoveryPending = ref(false)
const recoveryKeys = new Map<string, string>()
type RecoveryOperation = 'reconstruct' | 'reverify'

function documentedDefaults() {
  const offer = recovery.value?.reverify_archive
  const defaults = offer?.defaults
  return {
    simulators: (offer?.default_simulators ?? ['amici', 'copasi', 'pysces', 'tellurium', 'vcell']).join(', '),
    include_outputs: defaults?.include_outputs ?? false,
    user_description: defaults?.user_description ?? 'reverification',
    rel_tol: defaults?.rel_tol ?? 0.0001,
    abs_tol_min: defaults?.abs_tol_min ?? 0.001,
    abs_tol_scale: defaults?.abs_tol_scale ?? 0.00001,
    cache_buster: offer?.default_cache_buster ?? '0',
  }
}

function applyRecovery(value: VerificationRecovery | null) {
  const first = recovery.value == null && value != null
  recovery.value = value
  if (!value || !first) return
  reverifyDraft.value = documentedDefaults()
  reverifyConfirmed.value = false
}

function idempotencyKey(operation: RecoveryOperation, body: unknown): string {
  const key = `${props.workflowId}\n${operation}\n${JSON.stringify(body)}`
  const existing = recoveryKeys.get(key)
  if (existing) return existing
  const created = `k${recoveryKeys.size}-${Math.random().toString(36).slice(2)}`
  recoveryKeys.set(key, created)
  return created
}

function resetRecovery() {
  recovery.value = null
  linkedVerificationId.value = null
  recoveryError.value = null
  recoveryPending.value = false
  reverifyConfirmed.value = false
  recoveryKeys.clear()
}

function derivationLabel(kind: 'reconstruction' | 'reverification' | null | undefined): string | null {
  switch (kind) {
    case 'reconstruction':
      return 'New calculation'
    case 'reverification':
      return 'Re-verification'
    case undefined:
    case null:
      return null
    default: {
      const unexpected: never = kind
      return unexpected
    }
  }
}

async function submitRecovery(operation: RecoveryOperation) {
  const workflowId = props.workflowId
  const offer = recovery.value
  if (!workflowId || !offer) return
  if (operation === 'reverify' && !reverifyConfirmed.value) {
    recoveryError.value = 'Confirm that re-verification can start new simulations before submitting.'
    return
  }
  const body = operation === 'reconstruct'
    ? {
        run_ids: offer.reconstruct.run_ids ?? [],
        snapshot_sha256: offer.reconstruct.snapshot_sha256 ?? [],
        compare_settings: offer.reconstruct.compare_settings,
      }
    : {
        simulators: reverifyDraft.value.simulators.split(',').map(item => item.trim()).filter(Boolean),
        include_outputs: reverifyDraft.value.include_outputs,
        user_description: reverifyDraft.value.user_description,
        rel_tol: Number(reverifyDraft.value.rel_tol),
        abs_tol_min: Number(reverifyDraft.value.abs_tol_min),
        abs_tol_scale: Number(reverifyDraft.value.abs_tol_scale),
        cache_buster: reverifyDraft.value.cache_buster,
      }
  const key = idempotencyKey(operation, body)
  recoveryPending.value = true
  recoveryError.value = null
  try {
    const res = await $fetch<{ workflow_id: string }>(`${config.public.api_url}/verify/${workflowId}/${operation}`, {
      method: 'POST',
      body,
      headers: { 'Idempotency-Key': key },
    })
    if (workflowId !== props.workflowId) return
    linkedVerificationId.value = res.workflow_id
  } catch (err: any) {
    if (workflowId !== props.workflowId) return
    recoveryError.value = err?.data?.detail || err?.message || 'Recovery was not started'
  } finally {
    if (workflowId === props.workflowId) recoveryPending.value = false
  }
}

const status = computed(() => verificationDisplayStatus(workflowData.value, halted.value))
const isDone = computed(() => verificationIsDone(workflowData.value))

const statusBadgeColor = computed(() => {
  if (workflowData.value?.persistence_state === 'pending' && status.value !== 'PENDING') return 'warning'
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
      applyRecovery(res.recovery ?? null)
      emit('update:modelValue', res)

      if (verificationIsDone(res) && res.workflow_status === 'COMPLETED') {
        stopTimers()
        emit('completed', res)
      } else if (verificationIsDone(res) && (res.workflow_status === 'FAILED' || res.workflow_status === 'RUN_ID_NOT_FOUND')) {
        stopTimers()
        const err = res.workflow_error || `Verification failed with status: ${res.workflow_status}`
        errorMsg.value = err
        emit('failed', err)
      }
    }
  } catch (err: any) {
    if (controller.signal.aborted || workflowId !== props.workflowId) return
    const errText = err?.data?.detail || err?.message || 'Failed to poll verification workflow'
    unavailable.value = isUnavailableVerification(err)
    if (unavailable.value) applyRecovery(err?.data?.recovery ?? null)
    errorMsg.value = unavailable.value ? null : errText
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
  unavailable.value = false
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
  unavailable.value = false
  resetRecovery()
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
          <span v-if="workflowData?.persistence_state === 'pending' && workflowData?.report_available === false" class="text-xs">Saving report</span>
        </UBadge>
        <div>
          <h3 class="text-base font-semibold text-neutral-900 flex items-center gap-2">
            Verification Run
            <span v-if="!isDone && !unavailable" class="text-xs font-normal text-neutral-500">
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
            Run ID: <code class="font-mono bg-neutral-100 px-1 py-0.5 rounded text-[11px]">{{ workflowId }}</code>
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
          v-if="!isDone && !unavailable"
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
    <UAlert
      v-if="unavailable"
      color="warning"
      variant="subtle"
      icon="i-lucide-archive-x"
      title="Original report unavailable"
      description="The original verification result is no longer available."
      class="mt-4"
    />

    <div
      v-if="workflowData?.source_verification_id || workflowData?.derivation_kind || workflowData?.computed_at || workflowData?.comparator_version"
      class="mt-4 rounded-lg border border-neutral-200 bg-neutral-50 p-3 text-sm text-neutral-700 space-y-1"
    >
      <p v-if="derivationLabel(workflowData?.derivation_kind)" class="font-medium">
        {{ derivationLabel(workflowData?.derivation_kind) }}
      </p>
      <p v-if="workflowData?.source_verification_id">
        Source verification {{ workflowData.source_verification_id }}
      </p>
      <p v-if="workflowData?.computed_at">Computed {{ workflowData.computed_at }}</p>
      <p v-if="workflowData?.comparator_version">Comparator {{ workflowData.comparator_version }}</p>
      <p v-if="workflowData?.calculation === 'new_calculation'">
        New calculation from retained inputs. This is not an exact restoration of the original report.
      </p>
    </div>

    <div v-if="recovery?.reconstruct.eligible" class="mt-4 rounded-lg border border-neutral-200 p-3 space-y-2">
      <p class="text-sm text-neutral-700">{{ recovery.reconstruct.message }}</p>
      <UButton
        label="Reconstruct comparison"
        color="neutral"
        variant="outline"
        :loading="recoveryPending"
        @click="submitRecovery('reconstruct')"
      />
    </div>

    <div v-if="recovery?.reverify_archive.eligible" class="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 space-y-2">
      <p class="text-sm text-neutral-800">{{ recovery.reverify_archive.message }}</p>
      <p v-if="recovery.reverify_archive.may_start_simulations" class="text-sm font-medium text-amber-900">
        This can submit new simulation jobs. Cache reuse is not guaranteed.
      </p>
      <p class="text-xs text-neutral-600">
        Documented defaults: simulators {{ recovery.reverify_archive.default_simulators?.join(', ') }},
        description {{ reverifyDraft.user_description }},
        include outputs {{ reverifyDraft.include_outputs }},
        rel tol {{ reverifyDraft.rel_tol }},
        abs tol min {{ reverifyDraft.abs_tol_min }},
        abs tol scale {{ reverifyDraft.abs_tol_scale }},
        cache buster {{ reverifyDraft.cache_buster }}.
      </p>
      <p v-if="recovery.reverify_archive.suggested_simulators?.length" class="text-xs text-neutral-600">
        Suggested from cached runs only, not proof of the original selection:
        {{ recovery.reverify_archive.suggested_simulators.join(', ') }}.
      </p>
      <label class="flex items-start gap-2 text-sm text-neutral-800">
        <input v-model="reverifyConfirmed" type="checkbox" class="mt-1">
        <span>I understand re-verification can start new simulations.</span>
      </label>
      <UButton
        label="Start re-verification"
        color="warning"
        :disabled="!reverifyConfirmed"
        :loading="recoveryPending"
        @click="submitRecovery('reverify')"
      />
    </div>

    <p v-if="recoveryError" class="mt-3 text-sm text-red-700">{{ recoveryError }}</p>
    <p v-if="linkedVerificationId" class="mt-3 text-sm text-neutral-700">
      New verification
      <NuxtLink class="font-mono text-primary underline" :to="`?workflow_id=${linkedVerificationId}`">
        {{ linkedVerificationId }}
      </NuxtLink>
    </p>
  </div>
</template>
