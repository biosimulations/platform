<script setup lang="ts">
import {ref, resolveComponent, useTemplateRef} from 'vue'
import {upperFirst} from 'scule'
import type {TableColumn, TableRow} from '@nuxt/ui'
import type {BreadcrumbItem} from "#ui/components/Breadcrumb.vue";
import {normalize_text} from "~/functions/functions";
import type {TableFilter, TableFilterConfig, TablePagination} from "~/models/filtering";
import type {ProjectQueryStat, ProjectQueryStatFilter, ProjectSearchFilter, ProjectStub, ProjectStubPage,} from "~/models/projects";
import type {CoreRow} from "@tanstack/table-core"
import type {AppChip} from "~/models/common";

const UButton = resolveComponent('UButton')
const UDropdownMenu = resolveComponent('UDropdownMenu')

const route = useRoute()
const display_mode = ref<'cards' | 'table'>('cards')
const routes = route.path.split('/').filter(i => i && i.trim().length > 0)
const runtimeConfig = useRuntimeConfig()
const breadcrumbs = ref<BreadcrumbItem[]>([])
const advanced_filters_open = ref(false)
const fuzzy_search_term = ref('')
const chips = ref<AppChip[]>([])
const columns: TableColumn<ProjectStub>[] = [
  {
    accessorKey: 'id',
    header: 'Id',
    cell: ({ row }: { row: CoreRow<ProjectStub> }) => `${row.getValue('id')}`
  },
  /*{
    accessorKey: 'simulationRun',
    header: 'Simulation Run',
    cell: ({ row }: { row: CoreRow<ProjectStub> }) => `#${row.getValue('simulationRun')}`
  },*/
  {
    accessorKey: 'name',
    header: 'Name',
    cell: ({ row }: { row: CoreRow<ProjectStub> }) => `${row.getValue('name')}`
  },
  {
    accessorKey: 'summary',
    header: 'Abstract Summary',
    cell: ({ row }: { row: CoreRow<ProjectStub> }) => `${row.getValue('summary')}`
  },
  {
    accessorKey: 'created',
    header: 'Created',
    cell: ({ row }: { row: CoreRow<ProjectStub> }) => {
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
    cell: ({ row }: { row: CoreRow<ProjectStub> }) => {
      return new Date(row.getValue('updated')).toLocaleString('en-US', {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: true
      })
    }
  }
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

const table_pagination = ref({
  page: 0,
  perPage: 25,
  _total: 0
} as TablePagination)

const total_results = ref<number>(0)
const error_encountered = ref<string | undefined>(undefined)
const filter_suggestions = ref<ProjectQueryStatFilter[]>([])
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

  await fetch_projects()
})

async function fetch_projects() {
  error_encountered.value = undefined

  loading.value = true
  projects.value = []
  total_results.value = 0

  const populated_filters = searched_filters.value.filter(filter => filter.allowable_values.length > 0)

  try {
    // Results: the platform backend already returns ProjectStub-shaped rows with
    // image_url + model_format populated — no per-project /files call needed.
    // Pagination is 1-indexed here; the table is 0-indexed, so add one.
    $fetch<ProjectStubPage>(`${runtimeConfig.public.api_url}/projects`, {
      method: 'GET',
      query: {
        'searchTerm': fuzzy_search_term.value,
        'filters': JSON.stringify(populated_filters),
        'perPage': table_pagination.value.perPage,
        'page': table_pagination.value.page + 1
      }
    }).then((result: ProjectStubPage) => {
      projects.value = result.items
      total_results.value = result.total
    })

    // Facet counts come from a separate endpoint. Pass only the search term (not
    // the active filters) so the facet menu stays stable as filters are toggled.
    $fetch<ProjectQueryStat[]>(`${runtimeConfig.public.api_url}/projects/stats`, {
      method: 'GET',
      query: {
        'searchTerm': fuzzy_search_term.value
      }
    }).then((query_stats: ProjectQueryStat[]) => {
      filter_suggestions.value = query_stats_to_filter_groups(query_stats)
      searched_filters.value = filter_suggestions.value.map((f, _index) => ({ target: f.target, allowable_values: []}))
    })

    return
  } catch (error: any) {
    error_encountered.value = error.message
    throw error
  } finally {
    loading.value = false
  }
}

function query_stats_to_filter_groups(query_stats: ProjectQueryStat[] = []): ProjectQueryStatFilter[] {
  return query_stats
    .map((stat, index) => ({
      target: stat.target,
      values: [...(stat.valueFrequencies ?? [])]
        .filter(value_frequency => value_frequency.value !== undefined && value_frequency.value !== null)
        .sort((a, b) => b.count - a.count)
        .map((value_frequency) => {
          const label_without_count = `${value_frequency.value}`

          return {
            value: label_without_count,
            label: `${label_without_count} (${value_frequency.count})`,
          }
        }),
      _index: index,
    }))
    .filter(stat => stat.values.length > 0)
}

function clear_chip(chip: AppChip) {
  const found_category = searched_filters.value.filter(filter => filter.allowable_values.includes(chip.slug))[0]

  if (!found_category) return

  found_category.allowable_values.splice(found_category.allowable_values.indexOf(chip.slug), 1)
  chips.value.splice(chips.value.indexOf(chip), 1)

  fetch_projects()
}

function on_column_toggle() {
  table_filters.value._hidden_exist = !table.value?.tableApi.getIsAllColumnsVisible()
}

function change_pagination(new_page: number) {
  table_pagination.value.page = new_page
  fetch_projects()
}

function update_filter_chips() {
  if (!searched_filters.value || !searched_filters.value.length) return

  chips.value = []

  searched_filters.value.forEach((filter: ProjectSearchFilter) => {
    filter.allowable_values.forEach((value: string) => {
      const new_chip = {label: `${filter.target}: ${value}`, slug: value, removable: true} as AppChip
      chips.value.push(new_chip)
    })
  })

  fetch_projects()
}

function camel_to_title_case(str: string): string {
  if (!str) return '';

  return str
    .replace(/([A-Z])/g, ' $1')
    .replace(/^./, match => match.toUpperCase())
    .trim()
}

function visit_page(e: Event, row: TableRow<ProjectStub>) {
  navigateTo(`/projects/${row.id}`)
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
      <UInput type="text" class="flex-1" icon="i-heroicons-magnifying-glass" placeholder="Search projects" v-model="fuzzy_search_term" />

      <UButton
        label="Advanced Filters"
        color="neutral"
        variant="outline"
        trailing-icon="i-lucide-chevron-down"
        leading-icon="i-lucide-cog"
        :disabled="loading"
        :ui="{
         trailingIcon: advanced_filters_open ? 'rotate-180 transition-transform duration-200 i-lucide-chevron-down' : 'transition-transform duration-200 i-lucide-chevron-down'
        }"
        @click="advanced_filters_open = !advanced_filters_open" />

      <UButton
        label="Search"
        :disabled="loading"
        leading-icon="i-lucide-send"
        @click="fetch_projects()" />
    </div>

    <UCollapsible v-if="advanced_filters_open" v-model:open="advanced_filters_open">
      <template #content>
        <div class="flex items-center flex-wrap gap-4">
          <template v-for="filter of filter_suggestions" :key="filter.target">
            <div class="flex flex-col gap-1">
              <p class="text-sm font-bold">{{camel_to_title_case(filter.target)}}</p>
              <USelectMenu
                placeholder="Select Values"
                label-key="label"
                value-key="value"
                :items="filter.values"
                :loading="loading"
                :multiple="true"
                v-model="searched_filters[filter._index]!.allowable_values"
                @update:model-value="update_filter_chips()"
              />
            </div>
          </template>
        </div>
      </template>
    </UCollapsible>

    <AppChipList :chips="chips" @removed="clear_chip($event)" :loading="loading" v-if="!advanced_filters_open && chips && chips.length"></AppChipList>

<!--    <Loading v-if="!projects && !error_encountered" message="Fetching simulation projects..."/>-->

    <div class="w-full flex flex-col gap-4" v-if="projects && !error_encountered">
      <USeparator />

      <div class="w-full flex items-center justify-between gap-4">
        <h2 class="text-xl font-bold">Results</h2>
        <div class="flex items-center gap-4">
          <UDropdownMenu
            v-if="display_mode == 'table'"
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
                }))">
            <UButton
              label="Columns"
              color="neutral"
              variant="outline"
              trailing-icon="i-lucide-chevron-down" />
          </UDropdownMenu>

          <div class="w-max flex items-center justify-end rounded bg-neutral-100">
            <UButton size="sm" :label="`${display_mode == 'cards' ? '' : 'Table'}`" class="cursor-pointer" :color="`${display_mode == 'cards' ? 'subtle' : 'primary'}`" icon="i-lucide-list" type="button" @click="display_mode = 'table'"></UButton>
            <UButton size="sm" :label="`${display_mode == 'table' ? '' : 'Cards'}`" class="cursor-pointer" :color="`${display_mode == 'table' ? 'subtle' : 'primary'}`" icon="i-lucide-layout-grid" type="button" @click="display_mode = 'cards'"></UButton>
          </div>
        </div>
      </div>

      <UTable v-if="display_mode == 'table'"
        class="w-full"
        ref="table"
        :loading="loading"
        :data="projects"
        :columns="columns"
        @select="visit_page"
        sticky>
      </UTable>

      <div class="card_wrapper w-full gap-4" v-if="display_mode == 'cards'">
        <template v-if="loading">
          <USkeleton class="h-48" v-for="i in table_pagination.perPage" :key="i"></USkeleton>
        </template>
        <template v-else>
          <NuxtLink class="project_card cursor-pointer border no-underline border-neutral-300 rounded-lg overflow-hidden relative" :to="`/projects/${project.id}`" v-for="project in projects" :key="project.id">
            <NuxtImg
              :src="project.image_url"
              width="400"
              format="webp"
              fit="cover"
              @error="project.image_url='/images/project_placeholder.jpg'"
              alt="Project image"
              loading="lazy"
              class="absolute w-full h-full object-cover z-0 top-0 left-0 opacity-30"
            />
              <div class="card_text absolute h-full bottom-0 left-0 w-full flex flex-col justify-end items-start p-3">
                <h3 class="text-base font-bold">{{project.name}}</h3>
                <small class="w-full whitespace-nowrap overflow-hidden text-ellipsis">{{project.summary}}</small>
              </div>
            </NuxtLink>
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

    <ErrorPage v-if="error_encountered" message="An error occurred while fetching simulation projects" :error="error_encountered" @refresh="fetch_projects" />
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
  transition: transform 0.2s ease; /* Target only transform */
  will-change: transform; /* Hint to the browser to GPU-accelerate */
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
