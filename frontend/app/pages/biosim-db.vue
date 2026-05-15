<script setup lang="ts">
import { h, resolveComponent, useTemplateRef } from 'vue'
import { upperFirst } from 'scule'
import type { TableColumn } from '@nuxt/ui'
import type { Project } from '~/models/simulators'
import { DotLottieVue } from '@lottiefiles/dotlottie-vue'

const UButton = resolveComponent('UButton')
const UDropdownMenu = resolveComponent('UDropdownMenu')

const runtimeConfig = useRuntimeConfig()

const columns: TableColumn<Project>[] = [
  {
    accessorKey: 'id',
    header: ({ column }) => {
      const isSorted = column.getIsSorted()

      return h(UButton, {
        color: 'neutral',
        variant: 'ghost',
        label: 'Id',
        icon: isSorted ? (isSorted === 'asc' ? 'i-lucide-arrow-up-narrow-wide' : 'i-lucide-arrow-down-wide-narrow') : 'i-lucide-arrow-up-down',
        class: '-mx-2.5',
        onClick: () => column.toggleSorting(column.getIsSorted() === 'asc')
      })
    },
    cell: ({ row }) => `#${row.getValue('id')}`
  },
  {
    accessorKey: 'simulationRun',
    header: ({ column }) => {
      const isSorted = column.getIsSorted()

      return h(UButton, {
        color: 'neutral',
        variant: 'ghost',
        label: 'Simulation Run',
        icon: isSorted ? (isSorted === 'asc' ? 'i-lucide-arrow-up-narrow-wide' : 'i-lucide-arrow-down-wide-narrow') : 'i-lucide-arrow-up-down',
        class: '-mx-2.5',
        onClick: () => column.toggleSorting(column.getIsSorted() === 'asc')
      })
    },
    cell: ({ row }) => `#${row.getValue('name')}`
  },
  {
    accessorKey: 'created',
    header: ({ column }) => {
      const isSorted = column.getIsSorted()

      return h(UButton, {
        color: 'neutral',
        variant: 'ghost',
        label: 'Created',
        icon: isSorted ? (isSorted === 'asc' ? 'i-lucide-arrow-up-narrow-wide' : 'i-lucide-arrow-down-wide-narrow') : 'i-lucide-arrow-up-down',
        class: '-mx-2.5',
        onClick: () => column.toggleSorting(column.getIsSorted() === 'asc')
      })
    },
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
    header: ({ column }) => {
      const isSorted = column.getIsSorted()

      return h(UButton, {
        color: 'neutral',
        variant: 'ghost',
        label: 'Updated',
        icon: isSorted ? (isSorted === 'asc' ? 'i-lucide-arrow-up-narrow-wide' : 'i-lucide-arrow-down-wide-narrow') : 'i-lucide-arrow-up-down',
        class: '-mx-2.5',
        onClick: () => column.toggleSorting(column.getIsSorted() === 'asc')
      })
    },
    cell: ({ row }) => {
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
  /* {
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
  } */
]

const table = useTemplateRef('table')

const fetched_data = ref<Project[] | undefined>(undefined)
const error_encountered = ref<string | undefined>(undefined)

onMounted(async () => {
  await fetch_projects()
})

async function fetch_projects() {
  error_encountered.value = undefined
  fetched_data.value = undefined

  try {
    fetched_data.value = await $fetch(`${runtimeConfig.public.biosimulations_api_url}/projects`, {
      method: 'GET'
    })

    return
  } catch (error) {
    error_encountered.value = error instanceof Error ? error.message : String(error)
    throw error
  }
}
</script>

<template>
  <section
    class="w-full min-h-[calc(100vh-var(--ui-header-height))] p-6 max-w-(--ui-container) mx-auto flex flex-col gap-4"
    :class="{ 'items-center justify-center': !fetched_data || error_encountered, 'items-start justify-start': fetched_data && !error_encountered }"
  >
    <AppLoading
      v-if="!fetched_data && !error_encountered"
      message="Fetching simulation projects..."
    />

    <div
      v-if="fetched_data && !error_encountered"
      class="w-full"
    >
      <h3 class="w-full text-xl font-bold">
        Simulation Projects
      </h3>

      <div class="w-full flex justify-end py-3.5 border-b border-accented">
        <UDropdownMenu
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
          "
          :content="{ align: 'end' }"
        >
          <UButton
            label="Columns"
            color="neutral"
            variant="outline"
            trailing-icon="i-lucide-chevron-down"
          />
        </UDropdownMenu>
      </div>
      <UTable
        ref="table"
        class="w-full"
        :data="fetched_data"
        :columns="columns"
        sticky
      />
    </div>

    <div
      v-if="error_encountered"
      class="w-full md:w-max md:max-w-[700px] lg:max-w-[900px] flex flex-col items-center justify-center gap-2"
    >
      <DotLottieVue
        class="w-[150px] aspect-square"
        autoplay
        src="/animations/error.lottie"
      />
      <h1 class="text-2xl font-bold">
        An error occurred while fetching simulation projects
      </h1>
      <pre class="bg-neutral-100 rounded p-2">{{ error_encountered }}</pre>

      <div class="w-full flex-1 flex items-center justify-center gap-3 mt-4">
        <UButton
          color="primary"
          class="cursor-pointer"
          to="/"
          icon="i-lucide-home"
          label="Go Home"
        />
        <UButton
          color="neutral"
          class="cursor-pointer"
          variant="outline"
          icon="i-lucide-rotate-ccw"
          label="Retry"
          @click="fetch_projects()"
        />
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
