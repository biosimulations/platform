<script setup lang="ts">
import { ref, computed } from 'vue'
import type { VariableComparisonRow } from '~/models/verification'

const props = defineProps<{
  rows: VariableComparisonRow[]
  pairLabel?: string
  hasOutputs?: boolean
}>()

const emit = defineEmits<{
  'visualize': [varName: string]
}>()

const searchQuery = ref('')
const statusFilter = ref<'all' | 'pass' | 'fail' | 'outlier'>('all')
const currentPage = ref(1)
const perPage = ref(15)

const filteredRows = computed(() => {
  return props.rows.filter((row) => {
    // Search query
    if (searchQuery.value.trim().length > 0) {
      const q = searchQuery.value.trim().toLowerCase()
      if (!row.var_name.toLowerCase().includes(q)) {
        return false
      }
    }

    // Status filter
    if (statusFilter.value === 'pass') {
      return row.is_close === true
    }
    if (statusFilter.value === 'fail') {
      return row.is_close === false
    }
    if (statusFilter.value === 'outlier') {
      return row.outlier_simulators && row.outlier_simulators.length > 0
    }

    return true
  })
})

const totalPages = computed(() => {
  return Math.ceil(filteredRows.value.length / perPage.value) || 1
})

const paginatedRows = computed(() => {
  const start = (currentPage.value - 1) * perPage.value
  return filteredRows.value.slice(start, start + perPage.value)
})

const statusCounts = computed(() => {
  const total = props.rows.length
  const pass = props.rows.filter(r => r.is_close === true).length
  const fail = props.rows.filter(r => r.is_close === false).length
  const outlier = props.rows.filter(r => r.outlier_simulators && r.outlier_simulators.length > 0).length
  return { total, pass, fail, outlier }
})

function formatScientific(val?: number | null): string {
  if (val === null || val === undefined || isNaN(val)) return '—'
  if (val === 0) return '0.000'
  if (Math.abs(val) < 0.001 || Math.abs(val) >= 10000) {
    return val.toExponential(3)
  }
  return val.toFixed(4)
}

function formatScore(score?: number | null): string {
  if (score === null || score === undefined || isNaN(score)) return '—'
  return `${(score * 100).toFixed(1)}%`
}

function exportCsv() {
  const headers = ['Variable', 'Status', 'Maximum Error', 'Relative Error', 'Score', 'Outliers', 'Consensus']
  const csvRows = props.rows.map(r => [
    `"${r.var_name}"`,
    r.is_close === true ? 'PASS' : r.is_close === false ? 'FAIL' : 'UNKNOWN',
    r.maximum_error !== null ? r.maximum_error : '',
    r.relative_error !== null ? r.relative_error : '',
    r.score !== null ? r.score : '',
    `"${(r.outlier_simulators || []).join(', ')}"`,
    r.consensus_status
  ].join(','))

  const csvContent = [headers.join(','), ...csvRows].join('\n')
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.setAttribute('href', url)
  link.setAttribute('download', `verification_report_${props.pairLabel?.replace(/\s+/g, '_') || 'variables'}.csv`)
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}
</script>

<template>
  <div class="w-full bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-xl p-6 sm:p-7 shadow-sm">
    <!-- Header with Title & Export -->
    <div class="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-5 mb-5 border-b border-neutral-100 dark:border-neutral-800">
      <div>
        <h3 class="text-lg font-semibold text-neutral-900 dark:text-white flex items-center gap-2.5">
          <UIcon name="i-lucide-list-checks" class="size-5 text-primary shrink-0" />
          Observable Variables Concordance
          <span v-if="pairLabel" class="text-xs font-normal text-neutral-500 bg-neutral-100 dark:bg-neutral-800 px-2 py-0.5 rounded-full ml-1">
            {{ pairLabel }}
          </span>
        </h3>
        <p class="text-xs sm:text-sm text-neutral-500 mt-1">
          Detailed variable-by-variable numerical comparison, tolerance scoring, and consensus outlier diagnostics.
        </p>
      </div>

      <div class="flex items-center gap-2 self-end sm:self-auto shrink-0">
        <UButton
          icon="i-lucide-download"
          size="sm"
          variant="outline"
          color="neutral"
          label="Export CSV"
          @click="exportCsv"
        />
      </div>
    </div>

    <!-- Controls Bar: Search & Status Filters with room to breathe -->
    <div class="flex flex-col md:flex-row items-stretch md:items-center justify-between gap-4 mb-5">
      <div class="w-full md:w-80">
        <UInput
          v-model="searchQuery"
          icon="i-lucide-search"
          placeholder="Filter variables by name..."
          size="sm"
          class="w-full"
        />
      </div>

      <div class="flex items-center gap-2 flex-wrap">
        <span class="text-xs font-medium text-neutral-400 uppercase tracking-wider mr-1">Filter:</span>
        <UButton
          size="xs"
          :color="statusFilter === 'all' ? 'primary' : 'neutral'"
          :variant="statusFilter === 'all' ? 'solid' : 'ghost'"
          class="px-2.5 py-1 text-xs"
          @click="statusFilter = 'all'"
        >
          All ({{ statusCounts.total }})
        </UButton>
        <UButton
          size="xs"
          :color="statusFilter === 'pass' ? 'success' : 'neutral'"
          :variant="statusFilter === 'pass' ? 'solid' : 'ghost'"
          class="px-2.5 py-1 text-xs"
          @click="statusFilter = 'pass'"
        >
          Passing ({{ statusCounts.pass }})
        </UButton>
        <UButton
          size="xs"
          :color="statusFilter === 'fail' ? 'error' : 'neutral'"
          :variant="statusFilter === 'fail' ? 'solid' : 'ghost'"
          class="px-2.5 py-1 text-xs"
          @click="statusFilter = 'fail'"
        >
          Failing ({{ statusCounts.fail }})
        </UButton>
        <UButton
          size="xs"
          :color="statusFilter === 'outlier' ? 'warning' : 'neutral'"
          :variant="statusFilter === 'outlier' ? 'solid' : 'ghost'"
          class="px-2.5 py-1 text-xs"
          @click="statusFilter = 'outlier'"
        >
          Outliers ({{ statusCounts.outlier }})
        </UButton>
      </div>
    </div>

    <!-- Table Container with generous spacing -->
    <div class="overflow-x-auto border border-neutral-200 dark:border-neutral-800 rounded-xl shadow-2xs">
      <table class="w-full divide-y divide-neutral-200 dark:divide-neutral-800 text-left">
        <thead class="bg-neutral-50/80 dark:bg-neutral-800/60">
          <tr>
            <th scope="col" class="py-3.5 px-5 text-xs font-semibold uppercase tracking-wider text-neutral-500 dark:text-neutral-400 whitespace-nowrap min-w-[220px]">
              Variable Name
            </th>
            <th scope="col" class="py-3.5 px-5 text-xs font-semibold uppercase tracking-wider text-neutral-500 dark:text-neutral-400 whitespace-nowrap min-w-[110px]">
              Status
            </th>
            <th scope="col" class="py-3.5 px-5 text-xs font-semibold uppercase tracking-wider text-neutral-500 dark:text-neutral-400 whitespace-nowrap min-w-[160px]">
              Max Error (&Delta;<sub>max</sub>)
            </th>
            <th scope="col" class="py-3.5 px-5 text-xs font-semibold uppercase tracking-wider text-neutral-500 dark:text-neutral-400 whitespace-nowrap min-w-[150px]">
              Relative Error
            </th>
            <th scope="col" class="py-3.5 px-5 text-xs font-semibold uppercase tracking-wider text-neutral-500 dark:text-neutral-400 whitespace-nowrap min-w-[120px]">
              Tolerance Score
            </th>
            <th scope="col" class="py-3.5 px-5 text-xs font-semibold uppercase tracking-wider text-neutral-500 dark:text-neutral-400 whitespace-nowrap min-w-[220px]">
              Consensus &amp; Outliers
            </th>
            <th scope="col" class="py-3.5 px-5 text-xs font-semibold uppercase tracking-wider text-neutral-500 dark:text-neutral-400 text-right whitespace-nowrap w-24">
              Action
            </th>
          </tr>
        </thead>
        <tbody class="divide-y divide-neutral-100 dark:divide-neutral-800 bg-white dark:bg-neutral-900">
          <tr
            v-for="row in paginatedRows"
            :key="row.var_name"
            class="hover:bg-neutral-50/80 dark:hover:bg-neutral-800/50 transition-colors"
          >
            <!-- Variable Name -->
            <td class="py-3.5 px-5 font-mono text-xs sm:text-sm font-medium text-neutral-900 dark:text-white whitespace-nowrap">
              {{ row.var_name }}
            </td>

            <!-- Status Badge -->
            <td class="py-3.5 px-5 whitespace-nowrap">
              <UBadge
                v-if="row.is_close === true"
                color="success"
                variant="subtle"
                size="sm"
                class="font-semibold px-2.5 py-0.5"
              >
                PASS
              </UBadge>
              <UBadge
                v-else-if="row.is_close === false"
                color="error"
                variant="subtle"
                size="sm"
                class="font-semibold px-2.5 py-0.5"
              >
                FAIL
              </UBadge>
              <span v-else class="text-neutral-400 text-xs">—</span>
            </td>

            <!-- Max Error -->
            <td class="py-3.5 px-5 font-mono text-xs sm:text-sm whitespace-nowrap">
              <span :class="row.maximum_error !== null && row.maximum_error > 0.01 ? 'text-amber-600 dark:text-amber-400 font-semibold' : 'text-neutral-700 dark:text-neutral-300'">
                {{ formatScientific(row.maximum_error) }}
              </span>
            </td>

            <!-- Relative Error -->
            <td class="py-3.5 px-5 font-mono text-xs sm:text-sm whitespace-nowrap">
              <span :class="row.relative_error !== null && row.relative_error > 0.05 ? 'text-amber-600 dark:text-amber-400 font-semibold' : 'text-neutral-700 dark:text-neutral-300'">
                {{ formatScientific(row.relative_error) }}
              </span>
            </td>

            <!-- Score -->
            <td class="py-3.5 px-5 font-mono text-xs sm:text-sm text-neutral-700 dark:text-neutral-300 whitespace-nowrap">
              {{ formatScore(row.score) }}
            </td>

            <!-- Consensus & Outliers -->
            <td class="py-3.5 px-5 whitespace-nowrap">
              <span
                v-if="row.outlier_simulators && row.outlier_simulators.length > 0"
                class="text-xs sm:text-sm text-amber-600 dark:text-amber-400 flex items-center gap-1.5 font-medium whitespace-nowrap"
              >
                <UIcon name="i-lucide-alert-triangle" class="size-4 shrink-0" />
                <span>Outlier: {{ row.outlier_simulators.join(', ') }}</span>
              </span>
              <span
                v-else-if="row.consensus_status === 'concordant'"
                class="text-xs sm:text-sm text-emerald-600 dark:text-emerald-400 flex items-center gap-1.5 font-medium whitespace-nowrap"
              >
                <UIcon name="i-lucide-check-circle" class="size-4 shrink-0" />
                <span>Consensus Agreement</span>
              </span>
              <span
                v-else-if="row.consensus_status === 'discordant'"
                class="text-xs sm:text-sm text-rose-500 dark:text-rose-400 flex items-center gap-1.5 font-medium whitespace-nowrap"
              >
                <UIcon name="i-lucide-x-circle" class="size-4 shrink-0" />
                <span>Discordant</span>
              </span>
              <span v-else class="text-neutral-400 text-xs sm:text-sm">—</span>
            </td>

            <!-- Action -->
            <td class="py-3.5 px-5 text-right whitespace-nowrap">
              <UButton
                size="sm"
                variant="soft"
                color="primary"
                icon="i-lucide-line-chart"
                label="Plot"
                title="Plot this variable across simulators"
                class="font-medium"
                @click="emit('visualize', row.var_name)"
              />
            </td>
          </tr>

          <tr v-if="paginatedRows.length === 0">
            <td colspan="7" class="py-8">
              <UEmpty
                icon="i-lucide-search-x"
                title="No Variables Found"
                description="No observable variables match your search filter."
                variant="naked"
              />
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Pagination & Summary -->
    <div class="flex flex-col sm:flex-row items-center justify-between pt-4 mt-1 text-xs text-neutral-500 gap-3">
      <div>
        Showing
        <span class="font-semibold text-neutral-700 dark:text-neutral-300">
          {{ filteredRows.length > 0 ? (currentPage - 1) * perPage + 1 : 0 }}
        </span>
        to
        <span class="font-semibold text-neutral-700 dark:text-neutral-300">
          {{ Math.min(currentPage * perPage, filteredRows.length) }}
        </span>
        of
        <span class="font-semibold text-neutral-700 dark:text-neutral-300">
          {{ filteredRows.length }}
        </span>
        variables
      </div>

      <UPagination
        v-model:page="currentPage"
        :total="filteredRows.length"
        :items-per-page="perPage"
        size="xs"
      />
    </div>
  </div>
</template>
