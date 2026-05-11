<script setup lang="ts">
import { h, resolveComponent, ref, useTemplateRef } from 'vue'
import { upperFirst } from 'scule'
import type {TableColumn, TableRow} from '@nuxt/ui'
import { useClipboard } from '@vueuse/core'
import type {Project, SimulationRun} from "~/models/simulators";
import {DotLottieVue} from "@lottiefiles/dotlottie-vue";
import Loading from "~/components/Loading.vue";

const UButton = resolveComponent('UButton')
const UCheckbox = resolveComponent('UCheckbox')
const UBadge = resolveComponent('UBadge')
const UDropdownMenu = resolveComponent('UDropdownMenu')

const toast = useToast()
const { copy } = useClipboard()
const runtimeConfig = useRuntimeConfig()

export interface TableFilter {
  id: 'createdAt' | 'simulator',
  operator: 'starts_with' | 'ends_with' | 'contains' | 'less_than' | 'equal' | 'greater_than' | 'before' | 'after' | 'on' | 'is',
  value: any
}

const columns: TableColumn[] = [
  {
    accessorKey: 'id',
    header: 'Id',
    cell: ({row}) => row.getValue('id')
  },
  {
    accessorKey: 'name',
    header: 'Name',
    cell: ({row}) => row.getValue('name')
  },
  {
    accessorKey: 'status',
    header: 'Status',
    cell: ({ row }) => {
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
    cell: ({row}) => row.getValue('simulator')
  },
  {
    accessorKey: 'submitted',
    header: 'Submitted',

    cell: ({row}) => {
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
const column_filters = ref([
  {
    id: 'email',
    value: 'james'
  }
])

const table = useTemplateRef('table')
const loading = ref(true)
const fetched_data = ref<SimulationRun[]>([])
const error_encountered = ref<string | undefined>(undefined)
const fetch_user = ref(false)
const user_email = ref<string | undefined>(undefined)
const user_input = useTemplateRef('user_email_input')
const user_input_valid = ref(false)

onMounted(async () => {
  await fetch_runs()
})

function open_column_filter(column: any) {
  console.log(column)
}

function set_column_filter(column: any, operator: string, value: any) {

}

async function fetch_runs() {
  if (fetch_user.value && !user_email.value) return

  error_encountered.value = undefined
  fetched_data.value = []
  loading.value = true

  try {
    /*fetched_data.value = await $fetch(`${runtimeConfig.public.api_url}/runs}`, {
      method: 'POST'
      data: {
        type: 'all' | 'user'
        user: string | undefined
        sort: {
          id: 'createdAt',
          direction: 'asc' | 'desc'
        },
        filters: [
          {
            id: 'createdAt',
            operator: 'before',
            value: Date | string
          },
          {
            id: 'simulator',
            operator: 'contains',
            value: '...'
          }
        ]
      }
    })

    console.log(fetched_data.value)*/

    setTimeout(() => {
      fetched_data.value = [
        {
          "id": "sim-72f4e48c-9419-4e6a-b4c5-2a80973e77c1",
          "name": "Run 1",
          "simulator": "Simulator A",
          "simulatorVersion": "123",
          "simulatorDigest": "digest-c9a0d845-67b3-4f21-9d8a-4c12356789ab",
          "cpus": 2,
          "memory": 8,
          "maxTime": 120,
          "envVars": [
            "VAR_3",
            "ENV_FLAG_0",
            "VALUE_87"
          ],
          "purpose": "Performance Analysis",
          "email": "user0@example.com",
          "status": "FAILED",
          "runtime": 1400000,
          "projectSize": 567,
          "resultsSize": 23,
          "submitted": "2024-01-26T10:00:00.000Z",
          "updated": "2024-01-26T10:00:00.000Z"
        },
        {
          "id": "sim-04a71d6b-5f28-4f8e-a5d6-f1b90934d173",
          "name": "Run 2",
          "simulator": "Simulator B",
          "simulatorVersion": "456",
          "simulatorDigest": "digest-e6f2d75a-2c9b-4a7f-a83b-e90c1487d123",
          "cpus": 1,
          "memory": 4,
          "maxTime": 90,
          "envVars": [
            "VAR_1",
            "ENV_FLAG_1",
            "VALUE_92"
          ],
          "purpose": "Data Validation",
          "email": "user1@example.com",
          "status": "SUCCEEDED",
          "runtime": 840000,
          "projectSize": 123,
          "resultsSize": 11,
          "submitted": "2024-01-26T10:00:00.000Z",
          "updated": "2024-01-26T10:00:00.000Z"
        },
        {
          "id": "sim-9b329c5f-7d83-4e1d-a39f-d84916a2b93c",
          "name": "Run 3",
          "simulator": "Simulator C",
          "simulatorVersion": "789",
          "simulatorDigest": "digest-93f1a8d4-3c5e-4587-b92d-6f48d9d0a1b3",
          "cpus": 3,
          "memory": 12,
          "maxTime": 180,
          "envVars": [
            "VAR_7",
            "ENV_FLAG_0",
            "VALUE_35"
          ],
          "purpose": "Test Run",
          "email": "user2@example.com",
          "status": "CREATED",
          "runtime": 2160000,
          "projectSize": 890,
          "resultsSize": 32,
          "submitted": "2024-01-26T10:00:00.000Z",
          "updated": "2024-01-26T10:00:00.000Z"
        },
        {
          "id": "sim-5a1d63e7-8d96-448e-9970-6f8a9b3c4d12",
          "name": "Run 4",
          "simulator": "Simulator A",
          "simulatorVersion": "124",
          "simulatorDigest": "digest-74c8e0d2-93f5-4d1c-a8d6-5b1342968e7a",
          "cpus": 4,
          "memory": 10,
          "maxTime": 150,
          "envVars": [
            "VAR_4",
            "ENV_FLAG_1",
            "VALUE_16"
          ],
          "purpose": "Experimentation",
          "email": "user3@example.com",
          "status": "SUCCEEDED",
          "runtime": 1500000,
          "projectSize": 345,
          "resultsSize": 18,
          "submitted": "2024-01-26T10:00:00.000Z",
          "updated": "2024-01-26T10:00:00.000Z"
        },
        {
          "id": "sim-8c7e4b9a-6d29-4f9f-a39e-990a31845b5d",
          "name": "Run 5",
          "simulator": "Simulator B",
          "simulatorVersion": "457",
          "simulatorDigest": "digest-29c1d34f-036d-4a1e-95a8-f15a00b7c297",
          "cpus": 2,
          "memory": 6,
          "maxTime": 75,
          "envVars": [
            "VAR_9",
            "ENV_FLAG_0",
            "VALUE_42"
          ],
          "purpose": "Data Validation",
          "email": "user4@example.com",
          "status": "SUCCEEDED",
          "runtime": 750000,
          "projectSize": 234,
          "resultsSize": 8,
          "submitted": "2024-01-26T10:00:00.000Z",
          "updated": "2024-01-26T10:00:00.000Z"
        },
      ]
      loading.value = false
      return
    }, 500)
  } catch (error) {
    error_encountered.value = error.message
    throw error
  }
}

function process_click(_e: Event, row: TableRow<SimulationRun>) {
  console.log(row)
}

const checkValidity = () => {
  if (user_input.value?.inputRef.value) {
    user_input_valid.value = user_input.value.inputRef.validity.valid
  }
}
</script>

<template>
  <section class="w-full min-h-[calc(100vh-var(--ui-header-height))] p-6 max-w-(--ui-container) mx-auto flex flex-col gap-4" :class="{'items-center justify-center': !fetched_data || error_encountered, 'items-start justify-start': fetched_data && !error_encountered}">
    <Loading v-if="!fetched_data && !error_encountered" message="Fetching simulation runs..."/>

    <div class="w-full" v-if="fetched_data && !error_encountered">
      <div class="w-full flex items-center justify-between px-4 py-3.5 border-b border-accented">
        <h3 class="w-full text-xl font-bold">Simulation Runs</h3>
        <div class="w-max flex items-center gap-4">
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
                },
                onSelect(e: Event) {
                  e.preventDefault()
                }
              }))
            ">
          <UButton
            label="Columns"
            color="neutral"
            variant="outline"
            trailing-icon="i-lucide-chevron-down"
          />
        </UDropdownMenu>
        </div>
      </div>

      <Loading class="mx-auto w-max py-4" v-if="loading" message="Fetching simulation runs..."/>
      <UTable
        v-if="!loading"
        class="w-full"
        ref="table"
        :data="fetched_data"
        :columns="columns"
        sticky>
        <template v-for="column in columns" :key="column.accessorKey" #[`${column.accessorKey}-header`]="{ column: tableColumn }">
          <div class="flex items-center gap-2">
            <UButton
              color="neutral"
              variant="ghost"
              :label="tableColumn.columnDef.header"
              :icon="tableColumn.getIsSorted() ? (tableColumn.getIsSorted() === 'asc' ? 'i-lucide-arrow-up' : 'i-lucide-arrow-down') : 'i-lucide-arrow-up-down'"
              class="-mx-2.5"
              @click="tableColumn.toggleSorting(tableColumn.getIsSorted() === 'asc')"
            />

            <UPopover mode="hover">
              <UButton
                color="neutral"
                variant="ghost"
                icon="i-lucide-filter"
                @click="open_column_filter(tableColumn)"
              />
              <template #content>
                <div class="p-2">
                  <small><strong>Filter {{ tableColumn.columnDef.header }}</strong></small>
                </div>
              </template>
            </UPopover>
          </div>
        </template>
<!--        <template #name-cell="{ row }">
          <p @click="process_click(row)">{{row}}</p>
        </template>-->
      </UTable>
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
