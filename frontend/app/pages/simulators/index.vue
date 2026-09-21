<script setup lang="ts">
import { ref, computed, onMounted, useTemplateRef } from 'vue'
import { upperFirst } from 'scule'
import type { DropdownMenuItem, TableColumn } from '@nuxt/ui'
import { useClipboard } from '@vueuse/core'
import type { BreadcrumbItem } from '#ui/components/Breadcrumb.vue'
import type { CoreRow } from '@tanstack/table-core'
import type { TableFilterConfig, TableSort, TablePagination } from '~/models/filtering'
import {
  type TableSimulator,
  SimulatorCurationStatus,
} from '~/models/simulator-table'
import { useSimulators } from '~/composables/useSimulators'
import Loading from '~/components/Loading.vue'

const toast = useToast()
const { copy } = useClipboard()
const runtimeConfig = useRuntimeConfig()
const { fetchSimulators } = useSimulators()

const breadcrumbs = ref<BreadcrumbItem[]>([
  { label: 'Home', to: '/', icon: 'i-lucide-home' },
  { label: 'Simulators', to: '/simulators' }
])

const loading = ref(true)
const error_encountered = ref<string | undefined>(undefined)
const simulators_data = ref<TableSimulator[]>([])
const search_query = ref('')

const table = useTemplateRef('table')

// Table columns definition
const columns: (TableColumn<TableSimulator> & { accessorKey?: string })[] = [
  {
    accessorKey: 'name',
    header: 'Simulator',
    enableHiding: false,
  },
  {
    accessorKey: 'modelFormats',
    header: 'Model Formats',
  },
  {
    accessorKey: 'frameworks',
    header: 'Frameworks',
  },
  {
    accessorKey: 'curationStatus',
    header: 'Curation',
  },
  {
    accessorKey: 'description',
    header: 'Description',
  },
  {
    accessorKey: 'algorithms',
    header: 'Algorithms',
  },
  {
    accessorKey: 'interfaceTypes',
    header: 'Interfaces',
  },
  {
    accessorKey: 'supportedOperatingSystemTypes',
    header: 'OS',
  },
  {
    accessorKey: 'license',
    header: 'License',
  },
  {
    accessorKey: 'updated',
    header: 'Updated',
  },
  {
    id: 'actions',
    accessorKey: undefined,
    enableHiding: false,
    meta: {
      class: {
        td: 'text-right'
      }
    }
  }
]

// Filter configuration matching simulations/index.vue
const table_filters = ref<TableFilterConfig>({
  _hidden_exist: false,
  filters: {
    name: {
      id: 'name',
      operator: undefined,
      value: undefined,
      _filterType: 'text',
      _filterOptions: undefined
    },
    description: {
      id: 'description',
      operator: undefined,
      value: undefined,
      _filterType: 'text',
      _filterOptions: undefined
    },
    modelFormats: {
      id: 'modelFormats',
      operator: 'is_any',
      value: undefined,
      _filterType: 'enum',
      _filterOptions: []
    },
    frameworks: {
      id: 'frameworks',
      operator: 'is_any',
      value: undefined,
      _filterType: 'enum',
      _filterOptions: []
    },
    curationStatus: {
      id: 'curationStatus',
      operator: 'is_any',
      value: undefined,
      _filterType: 'enum',
      _filterOptions: [
        { label: '★ 5 - Image validated', value: SimulatorCurationStatus['Image validated'] },
        { label: '★ 4 - Image available', value: SimulatorCurationStatus['Image available'] },
        { label: '★ 3 - Parameters curated', value: SimulatorCurationStatus['Parameters curated'] },
        { label: '★ 2 - Algorithms curated', value: SimulatorCurationStatus['Algorithms curated'] },
        { label: '★ 1 - Registered', value: SimulatorCurationStatus['Registered with BioSimulators'] },
      ]
    },
    interfaceTypes: {
      id: 'interfaceTypes',
      operator: 'is_any',
      value: undefined,
      _filterType: 'enum',
      _filterOptions: []
    },
    supportedOperatingSystemTypes: {
      id: 'supportedOperatingSystemTypes',
      operator: 'is_any',
      value: undefined,
      _filterType: 'enum',
      _filterOptions: []
    },
    license: {
      id: 'license',
      operator: undefined,
      value: undefined,
      _filterType: 'text',
      _filterOptions: undefined
    }
  }
})

const columnVisibility = ref<Record<string, boolean>>({
  name: true,
  modelFormats: true,
  frameworks: true,
  curationStatus: true,
  description: false,
  algorithms: false,
  interfaceTypes: false,
  supportedOperatingSystemTypes: false,
  license: false,
  updated: false,
  actions: true
})

const table_sort = ref<TableSort>({
  id: 'name',
  direction: 'asc'
})

const table_pagination = ref<TablePagination>({
  page: 1,
  perPage: 25,
  _total: 0
})

const headerColumns = computed(() =>
  columns.filter((c): c is typeof c & { accessorKey: string } => typeof c.accessorKey === 'string')
)

// Populate dynamic filter options once data is fetched
function updateFilterOptions(simulators: TableSimulator[]) {
  const allFormats = new Set<string>()
  const allFrameworks = new Set<string>()
  const allInterfaces = new Set<string>()
  const allOS = new Set<string>()

  for (const s of simulators) {
    s.modelFormats.forEach(f => allFormats.add(f))
    s.frameworks.forEach(f => allFrameworks.add(f))
    s.interfaceTypes.forEach(i => allInterfaces.add(i))
    s.supportedOperatingSystemTypes.forEach(o => allOS.add(o))
  }

  table_filters.value.filters.modelFormats!._filterOptions = Array.from(allFormats).sort()
  table_filters.value.filters.frameworks!._filterOptions = Array.from(allFrameworks).sort()
  table_filters.value.filters.interfaceTypes!._filterOptions = Array.from(allInterfaces).sort()
  table_filters.value.filters.supportedOperatingSystemTypes!._filterOptions = Array.from(allOS).sort()
}

// Client-side filtering & search
const filtered_simulators = computed(() => {
  let result = simulators_data.value

  // Global search input
  const query = search_query.value.trim().toLowerCase()
  if (query) {
    result = result.filter((sim) => {
      const matchName = sim.name.toLowerCase().includes(query)
      const matchId = sim.id.toLowerCase().includes(query)
      const matchDesc = sim.description.toLowerCase().includes(query)
      const matchFormats = sim.modelFormats.some(f => f.toLowerCase().includes(query))
      const matchFrameworks = sim.frameworks.some(f => f.toLowerCase().includes(query))
      const matchAlgorithms = sim.algorithms.some(a => a.toLowerCase().includes(query))
      const matchAuthors = sim.authors.some(a => a.toLowerCase().includes(query))
      return matchName || matchId || matchDesc || matchFormats || matchFrameworks || matchAlgorithms || matchAuthors
    })
  }

  // Column-level filters
  for (const [key, filter] of Object.entries(table_filters.value.filters)) {
    if (!filter || filter.value === undefined || filter.value === null || filter.value === '') continue

    if (filter._filterType === 'text') {
      const targetVal = String(filter.value).toLowerCase()
      const op = filter.operator || 'contains'
      result = result.filter((sim) => {
        const fieldVal = String((sim as any)[key] || '').toLowerCase()
        if (op === 'equal') return fieldVal === targetVal
        if (op === 'starts_with') return fieldVal.startsWith(targetVal)
        if (op === 'ends_with') return fieldVal.endsWith(targetVal)
        return fieldVal.includes(targetVal)
      })
    } else if (filter._filterType === 'enum') {
      const selected = Array.isArray(filter.value) ? filter.value : [filter.value]
      if (selected.length === 0) continue

      result = result.filter((sim) => {
        const fieldVal = (sim as any)[key]
        if (Array.isArray(fieldVal)) {
          return selected.some(sel => fieldVal.includes(typeof sel === 'object' && sel !== null ? sel.value : sel))
        }
        const resolvedVal = typeof selected[0] === 'object' && selected[0] !== null ? selected.map(s => s.value) : selected
        return resolvedVal.includes(fieldVal)
      })
    }
  }

  // Sorting
  if (table_sort.value.id && table_sort.value.direction) {
    const sortId = table_sort.value.id as keyof TableSimulator
    const sign = table_sort.value.direction === 'asc' ? 1 : -1
    result = [...result].sort((a, b) => {
      const valA = a[sortId]
      const valB = b[sortId]
      if (typeof valA === 'number' && typeof valB === 'number') {
        return (valA - valB) * sign
      }
      return String(valA ?? '').localeCompare(String(valB ?? ''), undefined, { numeric: true }) * sign
    })
  }

  return result
})

// Pagination
const total_filtered_count = computed(() => filtered_simulators.value.length)

const paginated_simulators = computed(() => {
  const start = (table_pagination.value.page - 1) * table_pagination.value.perPage
  return filtered_simulators.value.slice(start, start + table_pagination.value.perPage)
})

async function load_simulators() {
  loading.value = true
  error_encountered.value = undefined
  try {
    const data = await fetchSimulators()
    simulators_data.value = data
    updateFilterOptions(data)
  } catch (err: any) {
    console.error('Error fetching simulators:', err)
    error_encountered.value = err.message || 'Failed to fetch simulators'
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await load_simulators()
})

function change_sort(column_id: string) {
  table_sort.value.direction = table_sort.value.id === column_id
    ? table_sort.value.direction === 'asc'
      ? 'desc'
      : 'asc'
    : 'asc'
  table_sort.value.id = column_id
}

function clear_filter(column_id: string) {
  if (!table_filters.value.filters[column_id]) return
  table_filters.value.filters[column_id]!.value = undefined
  if (table_filters.value.filters[column_id]._filterType !== 'enum') {
    table_filters.value.filters[column_id]!.operator = undefined
  } else {
    table_filters.value.filters[column_id]!.operator = 'is_any'
  }
}

function on_column_toggle() {
  table_filters.value._hidden_exist = !table.value?.tableApi.getIsAllColumnsVisible()
}

function hidden_cols_have_filters() {
  if (!table.value || !table_filters.value._hidden_exist) return false
  const hidden_columns = table.value.tableApi.getAllColumns().filter(column => !column.getIsVisible())
  const hidden_column_ids = hidden_columns.map(column => column.id)
  return hidden_column_ids.some(
    column_id => table_filters.value.filters[column_id]?.value !== undefined && table_filters.value.filters[column_id]?.operator !== undefined
  )
}

function clear_hidden_filters() {
  if (!table_filters.value._hidden_exist || !table.value) return
  const hidden_columns = table.value.tableApi.getAllColumns().filter(column => !column.getIsVisible())
  const hidden_column_ids = hidden_columns.map(column => column.id)
  hidden_column_ids.forEach(column_id => clear_filter(column_id))
  if (table_sort.value.id && hidden_column_ids.includes(table_sort.value.id)) {
    table_sort.value.id = undefined
    table_sort.value.direction = undefined
  }
}

function getActionItems(row: CoreRow<TableSimulator>): DropdownMenuItem[] {
  const sim = row.original
  const canRun = Boolean(sim.image && sim.curationStatus === SimulatorCurationStatus['Image validated'])

  return [
    {
      type: 'label',
      label: 'Actions'
    },
    {
      label: 'Run Simulation',
      icon: 'i-lucide-play',
      disabled: !canRun,
      onSelect() {
        navigateTo({
          path: '/simulations/run',
          query: {
            simulator: sim.id,
            simulatorVersion: sim.version
          }
        })
      }
    },
    {
      label: 'View Details',
      icon: 'i-lucide-info',
      onSelect() {
        navigateTo(`/simulators/${sim.id}`)
      }
    },
    ...(sim.url
      ? [
        {
          label: 'Website / Docs',
          icon: 'i-lucide-globe',
          onSelect() {
            window.open(sim.url!, '_blank', 'noopener,noreferrer')
          }
        }
      ]
      : []),
    {
      type: 'separator'
    },
    ...(sim.image
      ? [
        {
          label: 'Docker Image',
          icon: 'i-lucide-box',
          onSelect() {
            window.open(`https://github.com/orgs/biosimulators/packages/container/package/${sim.id}`, '_blank', 'noopener,noreferrer')
          }
        }
      ]
      : []),
    ...(sim.cli
      ? [
        {
          label: 'CLI (PyPI)',
          icon: 'i-lucide-terminal',
          onSelect() {
            window.open(`https://pypi.org/project/${sim.cli}`, '_blank', 'noopener,noreferrer')
          }
        }
      ]
      : []),
    ...(sim.pythonApi
      ? [
        {
          label: 'Python API (PyPI)',
          icon: 'i-lucide-code',
          onSelect() {
            window.open(`https://pypi.org/project/${sim.pythonApi}`, '_blank', 'noopener,noreferrer')
          }
        }
      ]
      : []),
    {
      type: 'separator'
    },
    {
      label: 'Copy Link',
      icon: 'i-lucide-copy',
      onSelect() {
        copy(`${runtimeConfig.public.base_url}/simulators/${sim.id}`)
        toast.add({
          title: 'Link copied to clipboard!',
          color: 'success',
          icon: 'i-lucide-circle-check'
        })
      }
    }
  ]
}

function formatDate(dateString: string | null) {
  if (!dateString) return 'N/A'
  return new Date(dateString).toLocaleDateString('en-US', {
    day: 'numeric',
    month: 'short',
    year: 'numeric'
  })
}
</script>

<template>
  <section class="w-full min-h-[calc(100vh-var(--ui-header-height))] p-6 max-w-(--ui-container) mx-auto flex flex-col gap-4">
    <UBreadcrumb class="mx-auto" :items="breadcrumbs" />

    <div class="page_header relative overflow-hidden w-full p-5 sm:p-8 bg-primary-500 text-white flex flex-col items-center justify-center gap-2 rounded-lg">
      <div class="background diamonds w-full h-full" />
      <h1 class="text-xl font-bold">Simulators</h1>
      <p class="text-center max-w-2xl text-sm md:text-base">
        Browse, search, and inspect biosimulation software tools, supported modeling frameworks, and algorithms!
      </p>
    </div>

    <Loading v-if="loading && simulators_data.length === 0" message="Fetching simulators registry..." />

    <div v-if="error_encountered" class="p-8 text-center text-error border border-dashed border-error/30 rounded-lg my-4">
      <UIcon name="i-lucide-alert-circle" class="w-8 h-8 mx-auto mb-2 text-error" />
      <p class="font-medium">Failed to load simulators</p>
      <p class="text-xs text-muted mt-1">{{ error_encountered }}</p>
      <UButton class="mt-4" color="primary" variant="outline" label="Retry" @click="load_simulators()" />
    </div>

    <div v-if="!error_encountered && simulators_data.length > 0" class="w-full">
      <!-- Controls bar -->
      <div class="w-full flex items-center justify-between gap-4 flex-wrap">
        <div class="w-full sm:w-72">
          <UInput
            v-model="search_query"
            placeholder="Search simulators (e.g. COPASI, SBML)..."
            icon="i-lucide-search"
            aria-label="Search simulators by name, ID, framework, or algorithm"
            class="w-full"
            clearable
          />
        </div>

        <div class="flex items-center gap-2">
          <UDropdownMenu
            :disabled="loading"
            :content="{ align: 'end' }"
            :items="
              table?.tableApi
                ?.getAllColumns()
                .filter((column) => column.getCanHide())
                .map((column) => ({
                  label: upperFirst(column.id),
                  type: 'checkbox' as const,
                  checked: column.getIsVisible(),
                  onUpdateChecked(checked: boolean) {
                    table?.tableApi?.getColumn(column.id)?.toggleVisibility(checked)
                    on_column_toggle()
                  },
                  onSelect(e: Event) {
                    e.preventDefault()
                  }
                }))"
          >
            <UButton
              label="Columns"
              color="neutral"
              variant="outline"
              aria-label="Toggle visible table columns"
              trailing-icon="i-lucide-chevron-down"
            />
          </UDropdownMenu>

          <UButton
            v-if="table_filters._hidden_exist && hidden_cols_have_filters()"
            label="Clear Hidden Filters"
            color="neutral"
            variant="outline"
            leading-icon="i-lucide-funnel-x"
            @click="clear_hidden_filters()"
          />
        </div>
      </div>

      <!-- Results Table -->
      <div class="w-full mt-6">
        <div class="flex items-center justify-between mb-2">
          <h2 class="text-lg font-bold text-color">Results</h2>
          <span class="text-xs text-muted">
            Showing {{ paginated_simulators.length }} of {{ total_filtered_count }} simulators
          </span>
        </div>

        <div v-if="paginated_simulators.length === 0" class="p-8 text-center text-muted border border-dashed border-gray-200 dark:border-gray-800 rounded-lg my-4">
          <UIcon name="i-lucide-inbox" class="w-8 h-8 mx-auto mb-2 text-muted" />
          <p class="font-medium">No simulators found</p>
          <p class="text-xs text-muted mt-1">Adjust your search query or clear active filters.</p>
          <UButton
            class="mt-4"
            color="neutral"
            variant="outline"
            label="Reset Search & Filters"
            @click="search_query = ''; Object.keys(table_filters.filters).forEach(k => clear_filter(k))"
          />
        </div>

        <UTable
          v-if="paginated_simulators.length > 0"
          ref="table"
          v-model:column-visibility="columnVisibility"
          class="w-full"
          :data="paginated_simulators"
          :columns="columns"
          sticky
        >
          <!-- Custom Header Slots with Sort and Filter Popovers -->
          <template v-for="column in headerColumns" :key="column.accessorKey" #[`${column.accessorKey}-header`]="{ column: tableColumn }">
            <div class="flex items-center gap-2">
              <UButton
                color="neutral"
                variant="ghost"
                :label="String(tableColumn.columnDef.header || '')"
                :icon="table_sort.id === column.accessorKey ? (table_sort.direction === 'asc' ? 'i-lucide-arrow-up' : 'i-lucide-arrow-down') : 'i-lucide-arrow-up-down'"
                class="-mx-2.5 cursor-pointer"
                @click="change_sort(column.accessorKey)"
              />

              <UPopover v-if="table_filters.filters[column.accessorKey]">
                <UButton
                  :color="table_filters.filters[column.accessorKey]!.value ? 'primary' : 'neutral'"
                  variant="ghost"
                  class="cursor-pointer"
                  icon="i-lucide-filter"
                  :aria-label="`Filter by ${tableColumn.columnDef.header}`"
                />
                <template #content>
                  <div class="p-3 w-64">
                    <small><strong>Filter "{{ tableColumn.columnDef.header }}"</strong></small>

                    <!-- Text Filter -->
                    <div v-if="table_filters.filters[column.accessorKey]!._filterType === 'text'" class="flex flex-col gap-2 mt-2">
                      <USelectMenu
                        v-model="table_filters.filters[column.accessorKey]!.operator"
                        placeholder="Select operator"
                        value-key="value"
                        :items="[
                          { label: 'Contains', value: 'contains' },
                          { label: 'Equals', value: 'equal' },
                          { label: 'Starts With', value: 'starts_with' },
                          { label: 'Ends With', value: 'ends_with' }
                        ]"
                      />
                      <UInput
                        v-model="table_filters.filters[column.accessorKey]!.value"
                        placeholder="Enter text..."
                        :disabled="!table_filters.filters[column.accessorKey]!.operator"
                      />
                      <div class="w-full flex items-center justify-between gap-4 mt-2">
                        <UButton
                          v-if="table_filters.filters[column.accessorKey]!.value"
                          color="error"
                          size="xs"
                          leading-icon="i-lucide-x"
                          label="Clear"
                          @click="clear_filter(column.accessorKey)"
                        />
                        <UButton
                          v-else
                          class="invisible"
                          size="xs"
                        />
                      </div>
                    </div>

                    <!-- Enum / Multi-select Filter -->
                    <div v-if="table_filters.filters[column.accessorKey]!._filterType === 'enum'" class="flex flex-col gap-2 mt-2">
                      <small class="font-semibold text-xs">Filter values:</small>
                      <USelectMenu
                        v-model="table_filters.filters[column.accessorKey]!.value"
                        multiple
                        placeholder="Select options..."
                        :items="table_filters.filters[column.accessorKey]!._filterOptions"
                      />
                      <div class="w-full flex items-center justify-between gap-4 mt-2">
                        <UButton
                          v-if="table_filters.filters[column.accessorKey]!.value"
                          color="error"
                          size="xs"
                          leading-icon="i-lucide-x"
                          label="Clear"
                          @click="clear_filter(column.accessorKey)"
                        />
                      </div>
                    </div>
                  </div>
                </template>
              </UPopover>
            </div>
          </template>

          <!-- Simulator Name Cell -->
          <template #name-cell="{ row }">
            <div class="flex items-center gap-2 py-1">
              <NuxtLink
                :to="`/simulators/${row.original.id}`"
                class="font-semibold text-primary-600 hover:underline text-sm"
              >
                {{ row.original.name }}
              </NuxtLink>
              <UBadge color="neutral" variant="subtle" size="sm" class="text-xs font-medium px-2 py-0.5">
                v{{ row.original.version }}
              </UBadge>
            </div>
          </template>

          <!-- Model Formats Cell -->
          <template #modelFormats-cell="{ row }">
            <div class="flex flex-wrap gap-1.5 max-w-xs">
              <UBadge
                v-for="format in row.original.modelFormats"
                :key="format"
                color="neutral"
                variant="outline"
                size="sm"
                class="text-xs font-medium px-2 py-0.5"
              >
                {{ format }}
              </UBadge>
              <span v-if="row.original.modelFormats.length === 0" class="text-xs text-muted">
                None
              </span>
            </div>
          </template>

          <!-- Frameworks Cell -->
          <template #frameworks-cell="{ row }">
            <div class="flex flex-wrap gap-1.5 max-w-xs">
              <UBadge
                v-for="fw in row.original.frameworks"
                :key="fw"
                color="primary"
                variant="subtle"
                size="sm"
                class="text-xs font-medium px-2 py-0.5"
              >
                {{ fw }}
              </UBadge>
              <span v-if="row.original.frameworks.length === 0" class="text-xs text-muted">
                None
              </span>
            </div>
          </template>

          <!-- Curation Status Cell (NO BADGE, clean stars) -->
          <template #curationStatus-cell="{ row }">
            <UTooltip :text="row.original.curationStatusMessage">
              <span class="inline-flex items-center select-none cursor-default">
                <span class="sr-only">Curation rating: {{ row.original.curationStatus }} out of 5 stars ({{ row.original.curationStatusMessage }})</span>
                <span aria-hidden="true" class="text-amber-500 font-bold text-sm tracking-wider">
                  {{ '★'.repeat(row.original.curationStatus) + '☆'.repeat(5 - row.original.curationStatus) }}
                </span>
              </span>
            </UTooltip>
          </template>

          <!-- Description Cell (Toggleable) -->
          <template #description-cell="{ row }">
            <p class="text-xs text-muted max-w-sm line-clamp-2" :title="row.original.description">
              {{ row.original.description || 'No description provided.' }}
            </p>
          </template>

          <!-- Algorithms Cell (Toggleable) -->
          <template #algorithms-cell="{ row }">
            <div class="flex flex-wrap gap-1.5 max-w-xs">
              <UBadge
                v-for="alg in row.original.algorithms.slice(0, 3)"
                :key="alg"
                color="neutral"
                variant="subtle"
                size="sm"
                class="text-xs font-medium px-2 py-0.5"
              >
                {{ alg }}
              </UBadge>
              <span v-if="row.original.algorithms.length > 3" class="text-xs text-muted self-center">
                +{{ row.original.algorithms.length - 3 }} more
              </span>
            </div>
          </template>

          <!-- Interfaces Cell (Toggleable) -->
          <template #interfaceTypes-cell="{ row }">
            <div class="flex flex-wrap gap-1.5">
              <UBadge
                v-for="iface in row.original.interfaceTypes"
                :key="iface"
                color="neutral"
                variant="outline"
                size="sm"
                class="text-xs font-medium px-2 py-0.5"
              >
                {{ iface }}
              </UBadge>
            </div>
          </template>

          <!-- Operating Systems Cell (Toggleable) -->
          <template #supportedOperatingSystemTypes-cell="{ row }">
            <div class="flex flex-wrap gap-1.5">
              <UBadge
                v-for="os in row.original.supportedOperatingSystemTypes"
                :key="os"
                color="neutral"
                variant="outline"
                size="sm"
                class="text-xs font-medium px-2 py-0.5"
              >
                {{ os }}
              </UBadge>
            </div>
          </template>

          <!-- License Cell (Toggleable) -->
          <template #license-cell="{ row }">
            <span class="text-xs font-mono text-muted">
              {{ row.original.license || 'N/A' }}
            </span>
          </template>

          <!-- Updated Cell (Toggleable) -->
          <template #updated-cell="{ row }">
            <span class="text-xs text-muted">
              {{ formatDate(row.original.updated) }}
            </span>
          </template>

          <!-- Actions Cell -->
          <template #actions-cell="{ row }">
            <UDropdownMenu :items="getActionItems(row)">
              <UButton
                color="neutral"
                variant="ghost"
                icon="i-lucide-ellipsis-vertical"
                :aria-label="`Actions for simulator ${row.original.name}`"
              />
            </UDropdownMenu>
          </template>
        </UTable>

        <!-- Pagination Controls -->
        <div class="w-full flex items-center justify-between gap-4 mt-6 flex-wrap">
          <div class="flex items-center gap-2">
            <small class="text-muted">Per page:</small>
            <USelectMenu
              v-model="table_pagination.perPage"
              :items="[10, 25, 50]"
              class="w-20"
            />
          </div>

          <UPagination
            v-model:page="table_pagination.page"
            :total="total_filtered_count"
            :items-per-page="table_pagination.perPage"
          />
        </div>
      </div>
    </div>
  </section>
</template>
