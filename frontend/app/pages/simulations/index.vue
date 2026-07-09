<script setup lang="ts">
import { h, resolveComponent, ref, useTemplateRef } from 'vue'
import { upperFirst } from 'scule'
import type {TableColumn} from '@nuxt/ui'
import { useClipboard } from '@vueuse/core'
import type {SimulationRuns, SimulationRun} from "~/models/simulators";
import {DotLottieVue} from "@lottiefiles/dotlottie-vue";
import Loading from "~/components/Loading.vue";
import type {TableFilterConfig, TableFilter, TableSort, TablePagination} from "~/models/filtering";
import type {BreadcrumbItem} from "#ui/components/Breadcrumb.vue";
import {normalize_text} from "~/functions/functions";
import type {CoreRow} from "@tanstack/table-core";

const UButton = resolveComponent('UButton')
const UBadge = resolveComponent('UBadge')
const UDropdownMenu = resolveComponent('UDropdownMenu')

const toast = useToast()
const { copy } = useClipboard()
const runtimeConfig = useRuntimeConfig()
const route = useRoute()
const routes = route.path.split('/').filter(i => i && i.trim().length > 0)
const breadcrumbs = ref<BreadcrumbItem[]>([])

onMounted(async () => {
  breadcrumbs.value = [{label: 'Home', to: '/', icon: 'i-lucide-home'}]
  routes.forEach((route, _index) => {
    const breadcrumb = {
      label: normalize_text(route),
      to: `/${route}`
    }

    breadcrumbs.value.push(breadcrumb)
  })

  await fetch_runs()
})

const columns: (TableColumn<SimulationRun> & { accessorKey?: string })[] = [
  {
    accessorKey: 'id',
    header: 'Id',
    cell: ({ row }: { row: CoreRow<SimulationRun> }) => row.getValue('id')
  },
  {
    accessorKey: 'name',
    header: 'Name',
    cell: ({ row }: { row: CoreRow<SimulationRun> }) => row.getValue('name')
  },
  {
    accessorKey: 'status',
    header: 'Status',
    cell: ({ row }: { row: CoreRow<SimulationRun> }) => {
      const color = ({
        SUCCEEDED: 'success' as const,
        FAILED: 'error' as const,
        CREATED: 'info' as const
      })[row.getValue('status') as string]

      return h(UBadge, { class: 'capitalize', variant: 'subtle', color }, () => row.getValue('status'))
    }
  },
  {
    accessorKey: 'simulator',
    header: 'Simulator',
    cell: ({ row }: { row: CoreRow<SimulationRun> }) => row.getValue('simulator')
  },
  {
    accessorKey: 'submitted',
    header: 'Submitted',
    cell: ({ row }: { row: CoreRow<SimulationRun> }) => {
      return new Date(row.getValue('submitted')).toLocaleString('en-US', {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: true
      })
    }
  },
  {
    id: 'actions',
    enableHiding: false,
    meta: {
      class: {
        td: 'text-right'
      }
    },
    cell: ({ row }) => {
      const items = [
        {
          type: 'label',
          label: 'Actions'
        },
        {
          label: 'Copy Sharable Link',
          icon: 'i-lucide-copy',
          onSelect() {
            copy(`${runtimeConfig.public.base_url}/simulations/${row.original.id}`)

            toast.add({
              title: 'Link copied to clipboard!',
              color: 'success',
              icon: 'i-lucide-circle-check'
            })
          }
        },
        {
          label: 'Export Run',
          icon: 'i-lucide-download',
          onSelect() {
            // Add export function here
          }
        },
        {
          type: 'separator'
        },
        {
          label: 'View Visualization',
          icon: 'i-lucide-chart-bar',
          onSelect() {
            // Add corresponding function here
          }
        },
        {
          label: 'View Logs',
          icon: 'i-lucide-file-text',
          onSelect() {
            // Add corresponding function here
          }
        },
        {
          label: 'Rerun Simulation',
          icon: 'i-lucide-rotate-ccw',
          onSelect() {
            // Add corresponding function here
          }
        },
        {
          label: 'Publish Simulation',
          icon: 'i-lucide-megaphone',
          onSelect() {
            // Add corresponding function here
          }
        },
        {
          type: 'separator'
        },
        {
          label: 'Delete Run',
          icon: 'i-lucide-trash',
          onSelect() {
            // Add corresponding function here
          }
        }
      ]

      return h(UDropdownMenu, {
        'content': {
          align: 'end'
        },
        items,
        'aria-label': 'Actions dropdown'
      }, () => h(UButton, {
        'icon': 'i-lucide-ellipsis-vertical',
        'color': 'neutral',
        'variant': 'ghost',
        'aria-label': 'Actions dropdown'
      }))
    }
  }]

const headerColumns = computed(() =>
  columns.filter((c): c is typeof c & { accessorKey: string } => typeof c.accessorKey === 'string')
)

const table = useTemplateRef('table')
const loading = ref(true)
const error_encountered = ref<string | undefined>(undefined)
const fetch_user = ref(false)
const user_email = ref<string | undefined>(undefined)
const user_input = useTemplateRef('user_email_input')
const user_input_valid = ref(false)
const table_filters = ref<TableFilterConfig>({
  _hidden_exist: false,
  filters: {
    'name': {
      id: 'name',
      operator: undefined,
      value: undefined,
      _filterType: 'text',
      _filterOptions: undefined,
    },
    'status': {
      id: 'status',
      operator: 'is_any',
      value: undefined,
      _filterType: 'enum',
      _filterOptions: ['SUCCEEDED', 'FAILED', 'CREATED'],
    },
    'simulator': {
      id: 'simulator',
      operator: undefined,
      value: undefined,
      _filterType: 'text',
      _filterOptions: undefined,
    },
    'submitted': {
      id: 'submitted',
      operator: undefined,
      value: undefined,
      _filterType: 'date',
      _filterOptions: undefined,
    },
  }
})
const table_sort = ref({
  id: undefined,
  direction: undefined
} as TableSort)
const table_pagination = ref({
  page: 1,
  perPage: 25,
  _total: 0
} as TablePagination)
const fetched_data = ref<SimulationRuns>({runs: [], pagination: table_pagination.value} as SimulationRuns)

async function fetch_runs() {
  if (fetch_user.value && !user_email.value) return

  loading.value = true
  error_encountered.value = undefined
  fetched_data.value = {runs: [], pagination: table_pagination.value} as SimulationRuns

  const valid_filter_keys = Object.keys(table_filters.value?.filters).filter(filter => table_filters.value.filters[filter]?.value && table_filters.value.filters[filter]?.operator)
  const valid_filters = valid_filter_keys.map(filter_key => table_filters.value.filters[filter_key])
  const trimmed_pagination = Object.keys(table_pagination.value).filter(key => !key.startsWith('_')).reduce((acc: Record<string, unknown>, key) => {
    acc[key] = (table_pagination.value as Record<string, unknown>)[key]
    return acc
  }, {} as Record<string, unknown>)

  if (fetched_data.value && table_pagination.value.perPage !== fetched_data.value.pagination.perPage) {
    table_pagination.value.page = 1
  }

  try {
    fetched_data.value = await $fetch(`${runtimeConfig.public.api_url}/simulations/runs`, {
      method: 'POST',
      body: {
        type: fetch_user.value ? 'user' : 'all',
        user: fetch_user.value ? user_email.value : undefined,
        sort: table_sort.value,
        filters: valid_filters,
        pagination: trimmed_pagination
      }
    })

    table_pagination.value = fetched_data.value.pagination

  } catch (error: any) {
    error_encountered.value = error.message
    throw error
  } finally {
    loading.value = false
  }
}

function clear_filter(column_id: string) {
  if (!table_filters.value.filters[column_id]) return

  table_filters.value.filters[column_id]!.value = undefined

  if (table_filters.value.filters[column_id]._filterType !== 'enum') {
    table_filters.value.filters[column_id]!.operator = undefined
  } else {
    table_filters.value.filters[column_id]!.operator = 'is_any'
  }

  fetch_runs()
}

function on_column_toggle() {
  table_filters.value._hidden_exist = !table.value?.tableApi.getIsAllColumnsVisible()
}

function _on_pagination_change(page: number, pageSize: number) {
  table_pagination.value.page = page
  table_pagination.value.perPage = pageSize

  fetch_runs()
}

function hidden_cols_have_filters() {
  if (!table.value || !table_filters.value._hidden_exist) return false

  const hidden_columns = table.value.tableApi.getAllColumns().filter(column => !column.getIsVisible())
  const hidden_column_ids = hidden_columns.map(column => column.id)
  return hidden_column_ids.some(column_id => table_filters.value.filters[column_id]?.value !== undefined && table_filters.value.filters[column_id]?.operator !== undefined)
}

function clear_hidden_filters() {
  if (!table_filters.value._hidden_exist || !table.value) return

  const hidden_columns = table.value.tableApi.getAllColumns().filter(column => !column.getIsVisible())
  const hidden_column_ids = hidden_columns.map(column => column.id)
  hidden_column_ids.forEach(column_id => clear_filter(column_id))

  // Check if any of the hidden columns are the currently sorted one, and unset the sort
  if (table_sort.value.id && hidden_column_ids.includes(table_sort.value.id)) {
    table_sort.value.id = undefined
    table_sort.value.direction = undefined
  }

  fetch_runs()
}

function change_sort(column_id: string) {
  table_sort.value.direction = table_sort.value.id == column_id ? (table_sort.value.direction === 'asc' ? 'desc' : 'asc') : 'asc'
  table_sort.value.id = column_id

  fetch_runs()
}

function change_pagination(new_page: number) {
  table_pagination.value.page = new_page
  fetch_runs()
}

const checkValidity = () => {
  if (user_input.value?.inputRef?.value) {
    user_input_valid.value = user_input.value!.inputRef.validity.valid
  }
}
</script>

<template>
  <section class="w-full min-h-[calc(100vh-var(--ui-header-height))] p-6 max-w-(--ui-container) mx-auto flex flex-col gap-4" :class="{'items-center justify-center': !fetched_data || error_encountered, 'items-start justify-start': !error_encountered}">
    <UBreadcrumb class="mx-auto" :items="breadcrumbs"></UBreadcrumb>
    <div class="page_header relative overflow-hidden w-full p-8 bg-primary-500 text-white flex flex-col items-center justify-center gap-2 rounded-lg">
      <div class="background diamonds w-full h-full"></div>
      <h1 class="text-xl font-bold">Simulation Runs</h1>
      <p>Retrieve your own simulation run results or browse what others have submitted!</p>
    </div>

    <Loading v-if="!fetched_data && !error_encountered" message="Fetching simulation runs..."/>

    <div class="w-full" v-if="fetched_data && !error_encountered">
      <div class="w-full flex items-center justify-between gap-4">
        <div class="w-max flex items-center gap-2">
          <p class="text-sm font-semibold" :class="{'text-muted': fetch_user, 'text-color': !fetch_user}">All Runs</p>
          <USwitch v-model="fetch_user" :disabled="loading" @change="fetch_runs()"/>
          <p class="text-sm font-semibold" :class="{'text-muted': !fetch_user, 'text-color': fetch_user}">My Runs</p>
          <UInput
            v-if="fetch_user"
            v-model="user_email"
            ref="user_email_input"
            type="email"
            @input="checkValidity"
            placeholder="Enter your email address"
            :ui="{ trailing: 'pe-1' }"
          >
            <template v-if="user_email && user_email.length" #trailing>
              <UButton
                color="primary"
                variant="solid"
                :disabled="!user_input_valid"
                size="xs"
                :loading="loading"
                icon="i-lucide-send"
                aria-label="Fetch runs"
                @click="fetch_runs"
              />
            </template>
          </UInput>
        </div>
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
                  table?.tableApi?.getColumn(column.id)?.toggleVisibility(!!checked)
                  on_column_toggle()
                },
                onSelect(e: Event) {
                  e.preventDefault()
                }
              }))">
          <UButton
            label="Columns"
            color="neutral"
            variant="outline"
            trailing-icon="i-lucide-chevron-down" />
        </UDropdownMenu>
        <UButton
          v-if="!loading && table_filters._hidden_exist && hidden_cols_have_filters()"
          label="Clear Hidden Filters"
          color="neutral"
          variant="outline"
          leading-icon="i-lucide-funnel-x"
          @click="clear_hidden_filters()">
        </UButton>
      </div>

      <Loading class="mx-auto w-max py-4" v-if="loading" message="Fetching simulation runs..."/>
      <UTable
        v-if="!loading"
        class="w-full"
        ref="table"
        :data="fetched_data.runs"
        :columns="columns"
        sticky>
        <template v-for="column in headerColumns" :key="column.accessorKey" #[`${column.accessorKey}-header`]="{ column: tableColumn }">
          <div class="flex items-center gap-2">
            <UButton
              color="neutral"
              variant="ghost"
              :label="tableColumn.columnDef.header"
              :icon="table_sort.id === column.accessorKey ? (table_sort.direction === 'asc' ? 'i-lucide-arrow-up' : 'i-lucide-arrow-down') : 'i-lucide-arrow-up-down'"
              class="-mx-2.5"
              @click="change_sort(column.accessorKey)"
            />

            <UPopover v-if="table_filters.filters[column.accessorKey]">
              <UButton
                :color="table_filters.filters[column.accessorKey]!.value ? 'primary' : 'neutral'"
                variant="ghost"
                icon="i-lucide-filter"
              />
              <template #content>
                <div class="p-2">
                  <small><strong>Filter "{{ tableColumn.columnDef.header }}"</strong></small>
                  <div class="flex flex-col gap-2 mt-2" v-if="table_filters.filters[column.accessorKey]!._filterType === 'text'">
                    <USelectMenu placeholder="Select operator" v-model="table_filters.filters[column.accessorKey]!.operator" value-key="value" :items="[{ label: 'Contains', value: 'contains' }, { label: 'Equals', value: 'equals' }, { label: 'Starts With', value: 'starts_with' }, { label: 'Ends With', value: 'ends_with' }]"/>
                    <UInput placeholder="Enter text..." v-model="table_filters.filters[column.accessorKey]!.value" :disabled="!table_filters.filters[column.accessorKey]!.operator"/>
                    <div class="w-full flex items-center gap-4" :class="{'justify-between': table_filters.filters[column.accessorKey]!.value, 'justify-end': !table_filters.filters[column.accessorKey]!.value}">
                      <UButton
                        v-if="table_filters.filters[column.accessorKey]!.value"
                        class="w-max whitespace-nowrap"
                        color="error"
                        size="sm"
                        leading-icon="i-lucide-x"
                        label="Clear Filter"
                        @click="clear_filter(column.accessorKey)"
                      />
                      <UButton
                        class="w-max whitespace-nowrap"
                        :disabled="!table_filters.filters[column.accessorKey]!.value || !table_filters.filters[column.accessorKey]!.operator"
                        color="primary"
                        size="sm"
                        leading-icon="i-lucide-check"
                        label="Apply"
                        @click="fetch_runs()"
                      />
                    </div>
                  </div>
                  <div class="flex flex-col gap-2 mt-2" v-if="table_filters.filters[column.accessorKey]!._filterType === 'enum'">
                    <small class="font-semibold">Show results with these statuses:</small>
                    <USelectMenu multiple :disabled="!table_filters.filters[column.accessorKey]!.operator" placeholder="Select statuses" v-model="table_filters.filters[column.accessorKey]!.value" :items="table_filters.filters[column.accessorKey]!._filterOptions"/>
                    <div class="w-full flex items-center gap-4" :class="{'justify-between': table_filters.filters[column.accessorKey]!.value, 'justify-end': !table_filters.filters[column.accessorKey]!.value}">
                      <UButton
                        v-if="table_filters.filters[column.accessorKey]!.value"
                        class="w-max whitespace-nowrap"
                        color="error"
                        size="sm"
                        leading-icon="i-lucide-x"
                        label="Clear Filter"
                        @click="clear_filter(column.accessorKey)"
                      />
                      <UButton
                        class="w-max whitespace-nowrap"
                        :disabled="!table_filters.filters[column.accessorKey]!.value || !table_filters.filters[column.accessorKey]!.operator"
                        color="primary"
                        size="sm"
                        leading-icon="i-lucide-check"
                        label="Apply"
                        @click="fetch_runs()"
                      />
                    </div>
                  </div>
                  <div class="flex flex-col gap-2 mt-2" v-if="table_filters.filters[column.accessorKey]!._filterType === 'date'">
                    <USelectMenu placeholder="Select operator" v-model="table_filters.filters[column.accessorKey]!.operator" value-key="value" :items="[{ label: 'Before', value: 'before' }, { label: 'On', value: 'on' }, { label: 'After', value: 'after' }]"/>
                    <UInputDate v-model="table_filters.filters[column.accessorKey]!.value" :disabled="!table_filters.filters[column.accessorKey]!.operator" />
                    <div class="w-full flex items-center gap-4" :class="{'justify-between': table_filters.filters[column.accessorKey]!.value, 'justify-end': !table_filters.filters[column.accessorKey]!.value}">
                      <UButton
                        v-if="table_filters.filters[column.accessorKey]!.value"
                        class="w-max whitespace-nowrap"
                        color="error"
                        size="sm"
                        leading-icon="i-lucide-x"
                        label="Clear Filter"
                        @click="clear_filter(column.accessorKey)"
                      />
                      <UButton
                        class="w-max whitespace-nowrap"
                        :disabled="!table_filters.filters[column.accessorKey]!.value || !table_filters.filters[column.accessorKey]!.operator"
                        color="primary"
                        size="sm"
                        leading-icon="i-lucide-check"
                        label="Apply"
                        @click="fetch_runs()"
                      />
                    </div>
                  </div>
                </div>
              </template>
            </UPopover>
          </div>
        </template>
<!--        <template #name-cell="{ row }">
          <p @click="process_click(row)">{{row}}</p>
        </template>-->
      </UTable>

      <USeparator class="mb-4"></USeparator>

      <div v-if="fetched_data" class="w-full flex items-center justify-between gap-4">
        <p class="text-muted ml-3">Showing results {{ (fetched_data.pagination.page - 1) * fetched_data.pagination.perPage + 1 }} - {{ Math.min(fetched_data.pagination.page * fetched_data.pagination.perPage, fetched_data.pagination._total ?? 0) }} of {{ fetched_data.pagination._total ?? 0 }}</p>
        <UPagination
          v-model="fetched_data.pagination.page"
          :total="fetched_data.pagination._total ?? 100"
          :items-per-page="fetched_data.pagination.perPage"
          :sibling-count="1"
          show-edges
          @update:page="change_pagination($event)"
        />
        <div class="w-max flex items-center gap-2">
          <p>Results Per Page:</p>
          <USelect :loading="loading" :disabled="loading" color="neutral" variant="outline" v-model="table_pagination.perPage" @change="fetch_runs()" :items="[5, 25, 50, 100]" />
        </div>
      </div>
    </div>

    <div class="w-full md:w-max md:max-w-[700px] lg:max-w-[900px] flex flex-col items-center justify-center gap-2" v-if="error_encountered">
      <DotLottieVue class="w-[150px] aspect-square" autoplay src="/animations/error.lottie" />
      <h1 class="text-2xl font-bold">An error occurred while fetching simulation runs</h1>
      <pre class="bg-neutral-100 rounded p-2">{{error_encountered}}</pre>

      <div class="w-full flex-1 flex items-center justify-center gap-3 mt-4">
        <UButton color="primary" class="cursor-pointer" to="/" icon="i-lucide-home" label="Go Home"></UButton>
        <UButton color="neutral" class="cursor-pointer" variant="outline" icon="i-lucide-rotate-ccw" label="Retry" @click="fetch_runs()"></UButton>
      </div>
    </div>
  </section>
</template>
<style>
  tr:hover {
    background-color: #fbfbfb !important;
    cursor: pointer;
  }

  td, th {
    padding: 0.5rem 1rem !important;
  }

  td:last-of-type {
    width: 100%;
  }
</style>
