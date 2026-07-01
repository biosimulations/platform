<script setup lang="ts">
import {ref, resolveComponent, useTemplateRef} from 'vue'
import {upperFirst} from 'scule'
import type {InputMenuItem, TableColumn, TableRow} from '@nuxt/ui'
import {DotLottieVue} from "@lottiefiles/dotlottie-vue";
import Loading from "~/components/Loading.vue";
import type {BreadcrumbItem} from "#ui/components/Breadcrumb.vue";
import {normalize_text} from "~/functions/functions";
import type {TableFilter, TableFilterConfig, TablePagination, TableSort} from "~/models/filtering";
import type {ProjectQueryStat, Projects, ProjectSearchFilter, ProjectSearchMenuItemValue, ProjectStub, ValueFrequency} from "~/models/projects";

const UButton = resolveComponent('UButton')
const UDropdownMenu = resolveComponent('UDropdownMenu')

const route = useRoute()
const display_mode = ref<'cards' | 'table'>('cards')
const routes = route.path.split('/').filter(i => i && i.trim().length > 0)
const runtimeConfig = useRuntimeConfig()
const breadcrumbs = ref<BreadcrumbItem[]>([])

const columns: TableColumn[] = [
  {
    accessorKey: 'id',
    header: 'Id',
    cell: ({row}) => `${row.getValue('id')}`
  },
  /*{
    accessorKey: 'simulationRun',
    header: 'Simulation Run',
    cell: ({row}) => `#${row.getValue('simulationRun')}`
  },*/
  {
    accessorKey: 'name',
    header: 'Name',
    cell: ({row}) => `${row.getValue('name')}`
  },
  {
    accessorKey: 'summary',
    header: 'Abstract Summary',
    cell: ({row}) => `${row.getValue('summary')}`
  },
  {
    accessorKey: 'created',
    header: 'Created',
    cell: ({ row }) => {
      return new Date(row.getValue('created')).toLocaleString('en-US', {
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
    accessorKey: 'updated',
    header: 'Updated',
    cell: ({row}) => {
      return new Date(row.getValue('updated')).toLocaleString('en-US', {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: true
      })
    }
  },
  /*{
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
            copy(process.env.BASE_URL + row.original.id)

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
  }*/
]

const table = useTemplateRef('table')
const loading = ref(true)
const table_filters = ref({
  _hidden_exist: false,
  filters: {
    'simulationRun': ref({
      id: 'simulationRun',
      operator: undefined,
      value: undefined,

      _filterType: 'text',
      _filterOptions: undefined,
    } as TableFilter),
    'created': ref({
      id: 'submitted',
      operator: undefined,
      value: undefined,

      _filterType: 'date',
      _filterOptions: undefined
    } as TableFilter),
    'updated': ref({
      id: 'updated',
      operator: undefined,
      value: undefined,

      _filterType: 'date',
      _filterOptions: undefined
    } as TableFilter),
  }
} as TableFilterConfig)
const table_sort = ref({
  id: undefined,
  direction: undefined
} as TableSort)
const table_pagination = ref({
  page: 0,
  perPage: 25,
  _total: 0
} as TablePagination)
const total_results = ref<number>(0)
const error_encountered = ref<string | undefined>(undefined)
const filter_suggestions = ref<InputMenuItem[]>([])
const selected_filter_items = ref<InputMenuItem[]>([])
const searched_filters = ref<ProjectSearchFilter[]>([])
const projects = ref<ProjectStub[]>([])

onMounted(async () => {
  breadcrumbs.value = [{label: 'Home', to: '/', icon: 'i-lucide-home'}]
  routes.forEach((route, _index) => {
    const breadcrumb = {
      label: normalize_text(route),
      to: `/${route}`
    }

    breadcrumbs.value.push(breadcrumb)
  })

  // update_filter_chips()
  await fetch_projects()
})

async function fetch_projects() {
  error_encountered.value = undefined

  loading.value = true
  projects.value = []
  total_results.value = 0

  try {
    const api_return = await $fetch(`${runtimeConfig.public.legacy_api_url}/projects/summary_filtered`, {
      method: 'GET',
      query: {
        // 'searchTerm': searched_filters.value,
        'filters': [searched_filters.value],
        'pageSize': table_pagination.value.perPage,
        'pageIndex': table_pagination.value.page
      }
    }) as Projects

    console.log(api_return)

    projects.value = await Promise.all(
      api_return.projectSummaries.map(async (p: any) => {
        const files = await $fetch(`${runtimeConfig.public.legacy_api_url}/files/${p.simulationRun.id}`, {})

        const image_url = (files as any[])
          .find(f =>
            f.name.endsWith('.png')
            || f.name.endsWith('.jpg')
            || f.name.endsWith('.jpeg')
          )?.url

        return {
          id: p.id,
          name: p.simulationRun.metadata[0].title,
          summary: p.simulationRun.metadata[0].abstract ? p.simulationRun.metadata[0].abstract.substring(0, 150) + '...' : '',
          created: p.created,
          updated: p.updated,
          simulationRun: p.simulationRun.id,
          image_url,
          model_format: p.model_format
        } as ProjectStub
      })
    ) as ProjectStub[]

    total_results.value = api_return.totalMatchingProjectSummaries
    filter_suggestions.value = query_stats_to_input_menu_items(api_return.queryStats)

    return
  } catch (error) {
    error_encountered.value = error.message
    throw error
  } finally {
    loading.value = false
  }
}

function query_stats_to_input_menu_items(query_stats: ProjectQueryStat[] = []): InputMenuItem[] {
  return query_stats.reduce((items, stat, stat_index) => {
    const sorted_value_frequencies = [...(stat.valueFrequencies ?? [])]
      .filter(value_frequency => value_frequency.value !== undefined && value_frequency.value !== null)
      .sort((a, b) => b.count - a.count)

    if (!sorted_value_frequencies.length) return items

    if (items.length > 0 && stat_index > 0) {
      items.push({
        type: 'separator'
      })
    }

    items.push({
      type: 'label',
      label: normalize_text(stat.target)
    })

    sorted_value_frequencies.forEach((value_frequency: ValueFrequency) => {
      items.push({
        label: `${stat.target}: ${value_frequency.value} (${value_frequency.count})`,
        value: {
          target: stat.target,
          allowable_value: value_frequency.value
        } satisfies ProjectSearchMenuItemValue
      })
    })

    return items
  }, [] as InputMenuItem[])
}

function clear_filter(column_id: string) {
  if (!table_filters.value.filters[column_id]) return

  table_filters.value.filters[column_id]!.value = undefined

  if (table_filters.value.filters[column_id]._filterType !== 'enum') {
    table_filters.value.filters[column_id]!.operator = undefined
  } else {
    table_filters.value.filters[column_id]!.operator = 'is_any'
  }

  fetch_projects()
}

function on_column_toggle() {
  table_filters.value._hidden_exist = !table.value?.tableApi.getIsAllColumnsVisible()
}

/*function on_pagination_change(page: number, pageSize: number) {
  table_pagination.value.page = page
  table_pagination.value.perPage = pageSize

  fetch_projects()
}*/

function hidden_cols_have_filters() {
  if (!table.value || !table_filters.value._hidden_exist) return false

  const hidden_columns = table.value.tableApi.getAllColumns().filter(column => !column.getIsVisible())
  const hidden_column_ids = hidden_columns.map(column => column.id)
  return hidden_column_ids.some(column_id => table_filters.value.filters[column_id]?.value !== undefined && table_filters.value.filters[column_id]?.operator !== undefined)
}

function clear_hidden_filters() {
  if (!table_filters.value._hidden_exist) return

  const hidden_columns = table.value.tableApi.getAllColumns().filter(column => !column.getIsVisible())
  const hidden_column_ids = hidden_columns.map(column => column.id)
  hidden_column_ids.forEach(column_id => clear_filter(column_id))

  // Check if any of the hidden columns are the currently sorted one, and unset the sort
  if (table_sort.value.id && hidden_column_ids.includes(table_sort.value.id)) {
    table_sort.value.id = undefined
    table_sort.value.direction = undefined
  }

  fetch_projects()
}

function change_sort(column_id: string) {
  table_sort.value.direction = table_sort.value.id == column_id ? (table_sort.value.direction === 'asc' ? 'desc' : 'asc') : 'asc'
  table_sort.value.id = column_id

  fetch_projects()
}

function change_pagination(new_page: number) {
  table_pagination.value.page = new_page
  fetch_projects()
}

/*function update_filter_chips() {
  if (!table_filters || !table_filters.value || !table_filters.value.filters.length) return

  chips.value = []

  table_filters.value.filters.forEach((filter: any) => {
    const new_chip = {label: `${columns.filter(c => c.accessorKey == filter.id)[0].label}: ${filter.operator} ${filter.value}`, slug: filter.id, removable: true} as AppChip
    chips.value.push(new_chip)
  })

  console.log(chips.value)
}*/

function update_searched_filters(selected_items: InputMenuItem[]) {
  searched_filters.value = selected_items.reduce((filters, item) => {
    const item_value = item!.value as ProjectSearchMenuItemValue | undefined

    if (!item_value?.target || !item_value.allowable_value) return filters

    const existing_filter = filters.find(filter => filter.target === item_value.target)

    if (existing_filter) {
      if (!existing_filter.allowable_values.includes(item_value.allowable_value)) {
        existing_filter.allowable_values.push(item_value.allowable_value)
      }
    } else {
      filters.push({
        target: item_value.target,
        allowable_values: [item_value.allowable_value]
      })
    }

    return filters
  }, [] as ProjectSearchFilter[])
}

function update_selected_filters(selected_items: InputMenuItem[]) {
  selected_filter_items.value = selected_items
  update_searched_filters(selected_items)

  fetch_projects()
}

function visit_page(e: Event, row: TableRow<ProjectStub>) {
  window.open(`https://biosimulations.org/projects/${row.id}`)
}
</script>

<template>
  <section class="w-full min-h-[calc(100vh-var(--ui-header-height))] p-6 max-w-(--ui-container) mx-auto flex flex-col gap-4" :class="{'items-center justify-center': !projects || error_encountered, 'items-start justify-start': !error_encountered}">
    <UBreadcrumb class="mx-auto" :items="breadcrumbs"></UBreadcrumb>

    <div class="page_header relative overflow-hidden w-full p-8 bg-primary-500 text-white flex flex-col items-center justify-center gap-2 rounded-lg">
      <div class="background isometric w-full h-full"></div>
      <h1 class="text-xl font-bold">Welcome to the BioSimulations database!</h1>
      <p>Explore the latest simulation projects and models.</p>
    </div>

    <div class="w-full flex items-center justify-between gap-4">
      <UInputMenu class="flex-1" icon="i-heroicons-magnifying-glass" placeholder="Search projects"
                  :ui="{trailingIcon: 'group-data-[state=open]:rotate-180 transition-transform duration-200'}"
                  multiple v-model="selected_filter_items" @update:model-value="update_selected_filters($event)" :items="filter_suggestions" />

      <div class="div w-max flex items-center justify-end rounded bg-neutral-100">
        <UButton size="sm" :label="`${display_mode == 'cards' ? '' : 'Table'}`" class="cursor-pointer" :color="`${display_mode == 'cards' ? 'subtle' : 'primary'}`" icon="i-lucide-list" type="button" @click="display_mode = 'table'"></UButton>
        <UButton size="sm" :label="`${display_mode == 'table' ? '' : 'Cards'}`" class="cursor-pointer" :color="`${display_mode == 'table' ? 'subtle' : 'primary'}`" icon="i-lucide-layout-grid" type="button" @click="display_mode = 'cards'"></UButton>
      </div>
    </div>

    <!--<AppChipList :chips="chips" @chip_removed="clear_filter($event)" v-if="display_mode == 'cards'"></AppChipList>-->

    <Loading v-if="!projects && !error_encountered" message="Fetching simulation projects..."/>

    <div class="w-full flex flex-col gap-4" v-if="projects && !error_encountered">
      <USeparator />

      <div class="w-full flex items-center justify-between gap-8" v-if="display_mode == 'table'">
        <h3 class="text-lg font-bold">Projects</h3>
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
                  on_column_toggle(column.id, checked)
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

      <Loading class="mx-auto w-max py-4" v-if="loading" message="Fetching projects..."/>

      <UTable v-if="!loading && display_mode == 'table'"
        class="w-full"
        ref="table"
        :data="projects"
        :columns="columns"
        @select="visit_page()"
        sticky>
        <template v-for="column in columns" :key="column.accessorKey" #[`${column.accessorKey}-header`]="{ column: tableColumn }">
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
                        @click="fetch_projects()"
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
                        @click="fetch_projects()"
                      />
                    </div>
                  </div>
                  <div class="flex flex-col gap-2 mt-2" v-if="table_filters.filters[column.accessorKey]!._filterType === 'date'">
                    <USelectMenu placeholder="Select operator" v-model="table_filters.filters[column.accessorKey]!.operator" value-key="value" :items="[{ label: 'Before', value: 'before' }, { label: 'On', value: 'on' }, { label: 'After', value: 'after' }]"/>
                    <UInputDate placeholder="Select date" v-model="table_filters.filters[column.accessorKey]!.value" :disabled="!table_filters.filters[column.accessorKey]!.operator" />
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
                        @click="fetch_projects()"
                      />
                    </div>
                  </div>
                </div>
              </template>
            </UPopover>
          </div>
        </template>
      </UTable>

      <div class="card_wrapper w-full gap-4" v-if="display_mode == 'cards'">
        <template v-if="loading">
          <USkeleton class="h-48" v-for="i in table_pagination.perPage" :key="i"></USkeleton>
        </template>
        <template v-else>
          <a class="project_card cursor-pointer border no-underline border-neutral-300 rounded-lg overflow-hidden relative" :href="`https://biosimulations.org/projects/${project.id}`" v-for="project in projects" :key="project.id">
            <img :src="project.image_url" @error="project.image_url='/images/project_placeholder.jpg'" alt="Project {{project.id}} image" loading="lazy" class="absolute w-full h-full object-cover z-0 top-0 left-0 opacity-30">
              <div class="card_text absolute h-full bottom-0 left-0 w-full flex flex-col justify-end items-start p-3">
                <h3 class="text-base font-bold">{{project.name}}</h3>
                <small class="w-full whitespace-nowrap overflow-hidden text-ellipsis">{{project.summary}}</small>
              </div>
            </a>
          <p class="empty_cards" v-if="!projects.length"><em>No projects found. Relax filter criteria to see broader results.</em></p>
        </template>
      </div>

      <USeparator class="mb-4"></USeparator>

      <div v-if="projects" class="w-full flex items-center justify-between gap-4">
        <p class="text-muted ml-3">Showing results {{ (table_pagination.page == 0 ? 1 : (table_pagination.page - 1) * table_pagination.perPage + 1) }} - {{ table_pagination.page == 0 ? table_pagination.perPage : (Math.min(table_pagination.page * table_pagination.perPage, total_results ?? 0)) }} of {{ total_results ?? 0 }}</p>
        <UPagination
          v-model="table_pagination.page"
          :total="total_results ?? 0"
          :items-per-page="table_pagination.perPage"
          :sibling-count="1"
          show-edges
          @update:page="change_pagination($event)"
        />
        <div class="w-max flex items-center gap-2">
          <p>Results Per Page:</p>
          <USelect :loading="loading" :disabled="loading" color="neutral" variant="outline" v-model="table_pagination.perPage" @change="fetch_projects()" :items="[5, 25, 50, 100]" />
        </div>
      </div>
    </div>

    <div class="w-full md:w-max md:max-w-[700px] lg:max-w-[900px] flex flex-col items-center justify-center gap-2" v-if="error_encountered">
      <DotLottieVue class="w-[150px] aspect-square" autoplay src="/animations/error.lottie" />
      <h1 class="text-2xl font-bold">An error occurred while fetching simulation projects</h1>
      <pre class="bg-neutral-100 rounded p-2">{{error_encountered}}</pre>

      <div class="w-full flex-1 flex items-center justify-center gap-3 mt-4">
        <UButton color="primary" class="cursor-pointer" to="/" icon="i-lucide-home" label="Go Home"></UButton>
        <UButton color="neutral" class="cursor-pointer" variant="outline" icon="i-lucide-rotate-ccw" label="Retry" @click="fetch_projects()"></UButton>
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

.empty_cards {
  grid-column: 1 / -1;
  text-align: center;
}

.card_wrapper {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
}

.project_card {
  width: 100%;
  aspect-ratio: 16 / 9;

  transition: all 0.2s ease;
}

.project_card:hover {
  transform: scale(1.025);
}

.project_card > .card_text {
  background: linear-gradient(to bottom, transparent, rgba(255, 255, 255, 1));
}

@media screen and (max-width: 768px) {
  .card_wrapper {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media screen and (max-width: 480px) {
  .card_wrapper {
    grid-template-columns: repeat(1, 1fr);
  }
}
</style>
