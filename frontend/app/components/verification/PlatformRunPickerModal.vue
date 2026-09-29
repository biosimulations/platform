<script setup lang="ts">
import { ref, computed, watch } from 'vue'

const props = defineProps<{
  open: boolean
}>()

const emit = defineEmits<{
  (e: 'update:open', val: boolean): void
  (e: 'select', val: {
    id: string
    name: string
    simulator: string
    simulatorVersion: string
    downloadUrl: string
  }): void
}>()

const config = useRuntimeConfig()
const isLoading = ref(false)
const searchQuery = ref('')
const runsList = ref<Array<{
  id: string
  biosimulationsRunId?: string | null
  name: string
  simulator: string
  simulatorVersion?: string
  status: string
  submitted?: string
}>>([])

const fetchError = ref<string | null>(null)

const isOpen = computed({
  get: () => props.open,
  set: (val: boolean) => emit('update:open', val)
})

watch(() => props.open, async (val) => {
  if (val && runsList.value.length === 0) {
    await fetchRuns()
  }
})

async function fetchRuns() {
  isLoading.value = true
  fetchError.value = null
  try {
    const res: any = await $fetch(`${config.public.api_url}/simulations/runs`, {
      method: 'POST',
      body: {
        type: 'all',
        pagination: { page: 1, perPage: 50 },
        sort: { id: 'submitted', direction: 'desc' },
        filters: []
      }
    })

    if (res && res.runs) {
      runsList.value = res.runs
    }
  } catch (err: any) {
    fetchError.value = err?.data?.detail || err?.message || 'Failed to load simulation runs from the platform database.'
  } finally {
    isLoading.value = false
  }
}

const filteredRuns = computed(() => {
  if (!searchQuery.value.trim()) return runsList.value
  const q = searchQuery.value.toLowerCase().trim()
  return runsList.value.filter(r =>
    (r.name && r.name.toLowerCase().includes(q)) ||
    (r.simulator && r.simulator.toLowerCase().includes(q)) ||
    (r.id && r.id.toLowerCase().includes(q)) ||
    (r.biosimulationsRunId && r.biosimulationsRunId.toLowerCase().includes(q))
  )
})

function onSelectRun(run: any) {
  const targetId = run.biosimulationsRunId || run.id
  const downloadUrl = `${config.public.legacy_api_url}/runs/${targetId}/download`
  emit('select', {
    id: targetId,
    name: run.name || `Run ${targetId.slice(0, 8)}`,
    simulator: run.simulator || 'unknown',
    simulatorVersion: run.simulatorVersion || '',
    downloadUrl
  })
  isOpen.value = false
}
</script>

<template>
  <UModal
    v-model:open="isOpen"
    title="Select OMEX Archive from Platform Runs"
    description="Choose a completed simulation run to extract and evaluate its COMBINE/OMEX archive."
    :ui="{ content: 'max-w-3xl' }"
  >
    <template #body>
      <div class="space-y-4 p-4">
        <!-- Search & Refresh -->
        <div class="flex items-center gap-2">
          <UInput
            v-model="searchQuery"
            placeholder="Search runs by model name, solver, or run ID..."
            icon="i-lucide-search"
            class="flex-1 text-xs"
            size="sm"
          />
          <UButton
            variant="ghost"
            color="neutral"
            size="sm"
            icon="i-lucide-refresh-cw"
            :loading="isLoading"
            title="Refresh runs"
            @click="fetchRuns"
          />
        </div>

        <!-- Runs List -->
        <div class="border border-neutral-200 rounded-lg max-h-95 overflow-y-auto">
          <UAlert
            v-if="fetchError"
            color="error"
            variant="subtle"
            icon="i-lucide-alert-circle"
            title="Failed to Retrieve Simulation Runs"
            :description="fetchError"
            class="m-3"
            :actions="[
              {
                label: 'Retry',
                icon: 'i-lucide-rotate-cw',
                color: 'error',
                variant: 'soft',
                onClick: fetchRuns
              }
            ]"
          />

          <UEmpty
            v-else-if="isLoading"
            loading
            title="Loading Platform Runs"
            description="Querying recent simulation runs from the platform database..."
            variant="naked"
          />

          <UEmpty
            v-else-if="filteredRuns.length === 0"
            icon="i-lucide-search-x"
            title="No Matching Runs"
            description="No completed simulation runs found matching your search."
            variant="naked"
          />

          <div v-else class="divide-y divide-neutral-100">
            <div
              v-for="run in filteredRuns"
              :key="run.id"
              class="p-3.5 hover:bg-neutral-50 dark:hover:bg-neutral-800/60 transition-colors flex items-center justify-between gap-3 text-xs"
            >
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2">
                  <span class="font-medium text-neutral-900 truncate">
                    {{ run.name }}
                  </span>
                  <UBadge
                    :color="run.status === 'SUCCEEDED' ? 'success' : run.status === 'FAILED' ? 'error' : 'neutral'"
                    variant="subtle"
                    size="xs"
                  >
                    {{ run.status }}
                  </UBadge>
                </div>

                <div class="flex items-center gap-3 mt-1 text-[11px] text-neutral-500 font-mono">
                  <span>ID: {{ (run.biosimulationsRunId || run.id).slice(0, 12) }}…</span>
                  <span>•</span>
                  <span class="capitalize">{{ run.simulator }} <span v-if="run.simulatorVersion">v{{ run.simulatorVersion }}</span></span>
                  <span v-if="run.submitted">•</span>
                  <span v-if="run.submitted">{{ new Date(run.submitted).toLocaleDateString() }}</span>
                </div>
              </div>

              <UButton
                size="xs"
                color="primary"
                variant="soft"
                icon="i-lucide-arrow-right"
                label="Select"
                @click="onSelectRun(run)"
              />
            </div>
          </div>
        </div>
      </div>
    </template>

    <template #footer>
      <div class="flex justify-end p-4 pt-0">
        <UButton
          label="Cancel"
          color="neutral"
          variant="ghost"
          size="sm"
          @click="isOpen = false"
        />
      </div>
    </template>
  </UModal>
</template>
