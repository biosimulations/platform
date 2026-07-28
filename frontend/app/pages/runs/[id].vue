<script setup lang="ts">
import type {SimulationRun} from "~/models/simulators";
import type {ProjectFile, SimulationRunSedDocument, SimulationRunSummary} from "~/models/simulation";
import {ref, computed, onMounted} from "vue";
import type {BreadcrumbItem} from "#ui/components/Breadcrumb.vue";
import {normalize_text} from "~/functions/functions";
import { useVisualizations } from "~/composables/useVisualizations";
import type { VisualizationList, Visualization } from "~/models/visualizations";

const runtimeConfig = useRuntimeConfig()
const route = useRoute()
const routes = route.path.split('/').filter(i => i && i.trim().length > 0)

const loading = ref(true)
const all_data_fetched = ref(false)
const error_encountered = ref<string | undefined>(undefined)
const breadcrumbs = ref<BreadcrumbItem[]>([])

const run_info = ref<SimulationRun | undefined>(undefined)
const run_summary = ref<SimulationRunSummary | undefined>(undefined)
const run_files = ref<ProjectFile[] | undefined>(undefined)

const img_zoomed = ref(false)

const fetched_data_array: boolean[] = []

const visualizationsLists = ref<VisualizationList[]>([])
const selectedVisualizationList = ref<VisualizationList | null>(null)
const selectedVisualization = ref<Visualization | null>(null)

// Watch when selected visualization list changes, pick its first chart by default
watch(selectedVisualizationList, (newList) => {
  if (newList && newList.visualizations.length > 0) {
    selectedVisualization.value = newList.visualizations[0];
  } else {
    selectedVisualization.value = null;
  }
});

const slider_enabled = ref<'Shown'|'Hidden'>('Hidden')
const plot_layout = computed(() => {
  const rangeslider = slider_enabled.value === 'Shown' ? { autorange: true, bordercolor: '#ff7b00', borderwidth: 1, thickness: 0.15 } : undefined

  return {
    autosize: true,
    legend: {
      x: 4.5, y: 1.5,
      orientation: 'v',
      traceorder: 'normal',
      font: {family: 'sans-serif', size: 9, color: '#000'},
      display: 'grid',
      bgcolor: '#DCDCDC',
      bordercolor: '#FFFFFF',
      borderwidth: 1,
    },
    xaxis: {
      tickfont: {size: 14, color: 'black'},
      font: {family: 'Roboto, sans-serif', size: 18, color: '#7f7f7f'},
      rangeslider: rangeslider
    }
  }
})
const plot_config = ref({
  scrollZoom: true,
  editable: false,
  toImageButtonOptions: {
    format: 'svg',
    height: 500,
    width: 700,
    scale: 1,
    filename: 'simulation_plot' // You can dynamically update this later
  },
  modeBarButtonsToRemove: [],
  displayLogo: false,
  showEditInChartStudio: true,
  plotlyServerURL: 'https://chart-studio.plotly.com',
  responsive: true,
})

const run_specifications = ref<SimulationRunSedDocument | undefined>(undefined)
const run_logs = ref<any>()

useSeoMeta({
  title: () => run_summary.value ? run_summary.value.name : 'Run',
  description: () => run_summary.value?.metadata?.[0]?.abstract || 'Explore this simulation run on BioSimulations.',
  author: () => run_summary.value?.metadata?.[0]?.creators?.map((c: any) => c.label).join(', ') || 'BioSimulations',
  keywords: () => run_summary.value?.metadata?.[0]?.keywords?.map((k: any) => k.label).join(', ') || 'biosimulations, run, simulation'
})

async function fetch_run() {
  const run_endpoints: any[] = [
    {
      url: `${runtimeConfig.public.legacy_api_url}/runs/${route.params.id}`,
      success: (data: SimulationRun) => {run_info.value = data},
    },
    {
      url: `${runtimeConfig.public.legacy_api_url}/runs/${route.params.id}/summary`,
      success: (data: SimulationRunSummary) => {run_summary.value = data},
    },
    {
      url: `${runtimeConfig.public.legacy_api_url}/files/${route.params.id}`,
      success: (data: ProjectFile[]) => {run_files.value = data},
    },
    {
      url: `${runtimeConfig.public.legacy_api_url}/specifications/${route.params.id}`,
      success: (data: SimulationRunSedDocument) => {run_specifications.value = data},
    },
    {
      url: `${runtimeConfig.public.legacy_api_url}/logs/${route.params.id}`,
      success: (data: any) => {run_logs.value = data},
    }
  ]

  run_endpoints.forEach(({url, success}) => {
    $fetch(url, {
      method: 'GET',
    })
      .then((data: any) => {
        console.log(`Fetched ${url}:`, data)
        success(data)
        fetched_data_array.push(true)

        if (fetched_data_array.length == run_endpoints.length) {
          all_data_fetched.value = true

          useVisualizations(route.params.id as string, run_files.value || [], run_specifications.value).then(lists => {
            visualizationsLists.value = lists;
            if (lists.length > 0) {
              selectedVisualizationList.value = lists[0];
              if (lists[0].visualizations.length > 0) {
                selectedVisualization.value = lists[0].visualizations[0];
              }
            }
          });
        }
      })
      .catch((error: any) => {
        console.log(`Error fetching ${url}:`, error)
        error_encountered.value = error.message
      })
  })
}

onMounted(async () => {
  breadcrumbs.value = [{label: 'Home', to: '/', icon: 'i-lucide-home'}]
  routes.splice(0, 1).forEach((route, _index) => {
    const breadcrumb = {
      label: normalize_text(route),
      to: `/${route}`
    }

    breadcrumbs.value.push(breadcrumb)
  })
  breadcrumbs.value.push({label: 'Simulation Details', to: ''})

  await fetch_run()

  if (run_info.value) {
    breadcrumbs.value[breadcrumbs.value.length - 1] = {label: run_info.value.name, to: ''}
  }
})
</script>

<template>
  <section class="w-full min-h-[calc(100vh-var(--ui-header-height))] p-6 max-w-(--ui-container) mx-auto flex flex-col gap-4" :class="{'items-center justify-center': !all_data_fetched || error_encountered, 'items-start justify-start': !error_encountered}">
    <template v-if="error_encountered">
      <ErrorPage v-if="error_encountered" message="An error occurred while fetching simulation run details" :error="error_encountered" @refresh="fetch_run" />
    </template>
    <template v-else>
      <template v-if="!all_data_fetched">
        <Loading class="mx-auto" v-if="!all_data_fetched && !error_encountered" message="Fetching simulation details..." />
      </template>
      <template v-else>
        <UBreadcrumb class="mx-auto" :items="breadcrumbs"></UBreadcrumb>

        <div class="page_header relative overflow-hidden w-full p-8 bg-primary-500 text-white flex flex-col items-center justify-center gap-2 rounded-lg">
          <h1 class="text-xl font-bold">{{normalize_text(run_info!.name)}}</h1>
          <p class="text-center" v-html="run_summary?.metadata?.[0]?.description"></p>
        </div>

        <div class="w-full flex flex-col gap-4">
          <div class="w-full flex items-start gap-4">
            <template v-if="!run_summary?.metadata?.[0]?.thumbnails?.[0]">
              <div class="w-187.5 h-auto bg-neutral-50 flex items-center justify-center p-4 rounded-lg border border-neutral-300">
                <p><em>No thumbnail available</em></p>
              </div>
            </template>
            <template v-else>
              <div class="thumbnail_wrapper group relative rounded-lg overflow-hidden w-full max-w-187.5 mx-auto">
                <div class="overlay w-full h-full absolute opacity-0 group-hover:opacity-100 bg-black/80 flex flex-col items-center justify-center gap-2 p-4 cursor-pointer transition-all" @click="img_zoomed = true">
                  <Icon name="i-lucide-zoom-in" class="text-white size-8" />
                  <p class="text-lg text-white font-bold">Click to expand</p>
                </div>
                <NuxtImg :src="`${runtimeConfig.public.legacy_api_url}/files/${run_info!.id}/${run_summary!.metadata![0]!.thumbnails[0]}/download?thumbnail=view`" alt="Simulation Thumbnail Image" class="w-full" />
                <vue-easy-lightbox
                  :visible="img_zoomed"
                  :imgs="[`${runtimeConfig.public.legacy_api_url}/files/${run_info!.id}/${run_summary!.metadata![0]!.thumbnails[0]}/download`]"
                  :index="0"
                  @hide="img_zoomed = false"
                />
              </div>
            </template>

            <div class="metadata_panel sticky top-[calc(1rem+var(--ui-header-height))] flex-1 flex flex-col p-4 rounded-lg border border-neutral-300 bg-neutral-50">
              <div class="w-full flex items-center justify-start gap-2">
                <Icon name="i-lucide-info" class="size-4.5 text-color" />
                <p class="text-color font-bold">Metadata</p>
              </div>
              <USeparator color="neutral" class="my-2" />
              <table class="metadata_table border-none p-0 m-0 border-collapse w-full">
                <tbody>
                  <tr>
                    <td>
                      <p class="text-base font-bold">Simulator:</p>
                    </td>
                    <td class="flex items-center justify-start gap-1">
                      <span class="text-color">{{ normalize_text(run_info?.simulator!) }}</span>
                      <Chip class="w-max" size="xs" :label="`v${run_info?.simulatorVersion}`" color="neutral" :removable="false" />
                    </td>
                  </tr>
                  <tr>
                    <td>
                      <p class="text-base font-bold">Submitted:</p>
                    </td>
                    <td>
                  <span class="text-color">
                    <NuxtTime v-if="run_info?.submitted" :datetime="run_info?.submitted" />
                    <span v-else>N/A</span>
                  </span>
                    </td>
                  </tr>
                  <tr>
                    <td>
                      <p class="text-base font-bold">Updated:</p>
                    </td>
                    <td>
                  <span class="text-color">
                    <NuxtTime v-if="run_info?.updated" :datetime="run_info?.updated" />
                    <span v-else>N/A</span>
                  </span>
                    </td>
                  </tr>
                  <tr>
                    <td>
                      <p class="text-base font-bold">Status:</p>
                    </td>
                    <td>
                  <span class="text-white p-1 rounded text-xs" :class="{'bg-green-500': run_info?.status === 'SUCCEEDED', 'bg-blue-500': run_info?.status === 'CREATED', 'bg-yellow-500': run_info?.status === 'RUNNING', 'bg-red-500': run_info?.status === 'FAILED'}">
                    {{ normalize_text(run_info?.status!) }}
                  </span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <USeparator />

          <div class="flex flex-col flex-1">
            <p class="text-lg"><strong>Chart</strong></p>
            <small class="mb-4">Select a plot defined in a SED-ML or <a class="underline hover:text-blue-500" href="https://vega.github.io/vega/" target="_blank">Vega&#8599;</a> document in the simulation project, or design a custom grid of plots. (Vega is a powerful format for describing data visualizations. By capturing how data should be used to paint visual elements, Vega enables reusable visualizations that can be re-painted with data from multiple simulations. Vega can also capture interactive and publication-quality graphics.)</small>

            <!-- Dynamic Chart Visualization -->
            <div v-if="visualizationsLists.length > 0">
              <div v-if="selectedVisualization" class="chart-container w-full p-6 border border-neutral-200 rounded-xl bg-neutral-50/50 shadow-sm min-h-[400px]">
                <div class="w-full flex items-center justify-between gap-4">
                  <UFormField class="w-max" label="Range Slider Display">
                    <USelectMenu
                      v-model="slider_enabled"
                      :items="['Shown', 'Hidden']"
                      label-key="slider"
                      placeholder="Select an option"
                      class="w-full"
                    />
                  </UFormField>

                  <div class="flex flex-col md:flex-row justify-end gap-4" v-if="visualizationsLists.length > 0">
                    <UFormField class="w-max" label="Visualization Group">
                      <USelectMenu
                        v-model="selectedVisualizationList"
                        :items="visualizationsLists"
                        label-key="title"
                        placeholder="Select a visualization group..."
                      />
                    </UFormField>
                    <UFormField class="w-max" label="Specific Chart" v-if="selectedVisualizationList">
                      <USelectMenu
                        v-model="selectedVisualization"
                        :items="selectedVisualizationList.visualizations"
                        label-key="name"
                        placeholder="Select a chart..."
                      />
                    </UFormField>
                  </div>
                </div>

                <div v-if="selectedVisualization._isLoading" class="w-full h-[400px] flex flex-col items-center justify-center gap-4 text-gray-500 mt-4">
                  <UIcon name="i-lucide-loader-2" class="w-8 h-8 animate-spin" />
                  <p>Loading visualization data...</p>
                </div>
                <div v-else-if="selectedVisualization._error" class="w-full h-[400px] flex flex-col items-center justify-center gap-4 text-red-500 mt-4">
                  <UIcon name="i-lucide-alert-circle" class="w-8 h-8" />
                  <p>{{ selectedVisualization._error }}</p>
                </div>
                <template v-else>
                  <VegaVisualization
                    v-if="selectedVisualization.renderer === 'Vega'"
                    :spec="selectedVisualization.vegaSpec"
                    :name="selectedVisualization.name"
                  />

                  <div v-else-if="selectedVisualization.renderer === 'Plotly' && !selectedVisualization.userDesigned" class="relative w-full aspect-video min-h-[400px] mt-4">
                    <PlotlyChart
                      :data="selectedVisualization.plotlyDataLayout?.data || []"
                      :layout="Object.assign({}, plot_layout, selectedVisualization.plotlyDataLayout?.layout || {})"
                      :config="plot_config"
                      class="absolute inset-0"
                    />
                  </div>

                  <DesignChart
                    v-else-if="selectedVisualization.userDesigned"
                    :visualization="selectedVisualization"
                    :default-layout="plot_layout"
                    :default-config="plot_config"
                  />
                </template>
              </div>
            </div>
            <div v-else class="p-8 text-center text-gray-500 bg-gray-50 border rounded">
              <p>No visualizations available for this run.</p>
            </div>
          </div>
        </div>

        <div class="w-full p-6 border border-neutral-200 rounded-xl bg-neutral-50/50 shadow-sm">
          <UCollapsible class="w-full flex flex-col gap-2" :default-open="true">
            <UButton
              class="group w-full text-lg font-bold text-color cursor-pointer p-0"
              label="Files & Outputs"
              color="neutral"
              variant="link"
              trailing-icon="i-lucide-chevron-down"
              :ui="{
            trailingIcon: 'group-data-[state=open]:rotate-180 transition-transform duration-200'
          }"
              block
            />

            <template #content>
              <div class="mt-4">
                <FilesOutputsTable :summary="run_summary" :files="run_files" />
              </div>
            </template>
          </UCollapsible>
        </div>

        <div class="w-full p-6 border border-neutral-200 rounded-xl bg-neutral-50/50 shadow-sm">
          <UCollapsible class="w-full flex flex-col gap-2" :default-open="true">
            <UButton
              class="group w-full text-lg font-bold text-color cursor-pointer p-0"
              label="Logs"
              color="neutral"
              variant="link"
              trailing-icon="i-lucide-chevron-down"
              :ui="{
                trailingIcon: 'group-data-[state=open]:rotate-180 transition-transform duration-200'
              }"
              block
            />

            <template #content>
              <div class="mt-4">
                <SimulationLogs :logs="run_logs" />
              </div>
            </template>
          </UCollapsible>
        </div>
      </template>
    </template>
  </section>
</template>
<style scoped lang="scss">
.metadata_table {
  tr td {
    padding-top: 0.5rem;
  }
}
</style>
