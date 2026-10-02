<script setup lang="ts">
import type { ProjectsPagePayload, PageProjectOverview, PageProjectSimulationRun, PageProjectFile, PageSimulationRunSedDocument } from "~/models/page";
import {ref, computed, onMounted} from "vue";
import type {BreadcrumbItem} from "#ui/components/Breadcrumb.vue";
import {normalize_text} from "~/functions/functions";
import { useVisualizations } from "~/composables/useVisualizations";
import type { VisualizationList } from "~/models/visualizations";

const runtimeConfig = useRuntimeConfig()
const route = useRoute()
const routes = route.path.split('/').filter(i => i && i.trim().length > 0)

const all_data_fetched = ref(false)
const error_encountered = ref<string | undefined>(undefined)
const breadcrumbs = ref<BreadcrumbItem[]>([])

const project_overview = ref<PageProjectOverview | undefined>(undefined)
const run_summary = ref<PageProjectSimulationRun | undefined>(undefined)
const run_files = ref<PageProjectFile[] | undefined>(undefined)
const run_specifications = ref<PageSimulationRunSedDocument[] | undefined>(undefined)

const img_zoomed = ref(false)

const visualizationsLists = ref<VisualizationList[]>([])

const allVisualizations = computed(() => {
  return visualizationsLists.value.flatMap(list => list.visualizations).filter(v => !v.userDesigned);
})

const plot_layout = computed(() => {
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
    filename: 'simulation_plot'
  },
  modeBarButtonsToRemove: [],
  displayLogo: false,
  showEditInChartStudio: true,
  plotlyServerURL: 'https://chart-studio.plotly.com',
  responsive: true,
})

const isDescriptionExpanded = ref(false)
const descriptionContent = computed(() => {
  const raw = run_summary.value?.description;
  if (!raw) return { html: '', isLong: false };
  const stripped = raw.replace(/<\/?[^>]+(>|$)/g, "");
  return { html: raw, isLong: stripped.length >= 250 };
})

useSeoMeta({
  title: () => run_summary.value ? run_summary.value.name : 'Project',
  description: () => run_summary.value?.abstract || 'Explore this project on BioSimulations.',
  author: () => run_summary.value?.creators?.map(c => c.label).join(', ') || 'BioSimulations',
  keywords: () => run_summary.value?.keywords?.map(k => k.label).join(', ') || 'biosimulations, project'
})

async function fetch_run() {
  try {
    const payload = await $fetch<ProjectsPagePayload>(`${runtimeConfig.public.api_url}/projects/${route.params.id}/page`);
    project_overview.value = payload.project;
    run_summary.value = payload.simulationRun;
    run_files.value = payload.files;
    run_specifications.value = payload.specifications;

    all_data_fetched.value = true;

    useVisualizations(payload.simulationRun.id, payload.files, payload.specifications).then((lists) => {
      visualizationsLists.value = lists;
    });

  } catch (error: any) {
    console.error(`Error fetching project data:`, error);
    error_encountered.value = error.message;
  }
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
  breadcrumbs.value.push({label: 'Project Details', to: ''})

  await fetch_run()

  if (run_summary.value) {
    breadcrumbs.value[breadcrumbs.value.length - 1] = {label: run_summary.value.name, to: ''}
  }
})

interface MetadataValue {
  label: string;
  url?: string;
}
interface MetadataItem {
  icon: string;
  title: string;
  values: MetadataValue[];
}
interface MetadataSection {
  label: string;
  defaultOpen: boolean;
  items: MetadataItem[];
}

function getMetadataSections(): MetadataSection[] {
  const sections: MetadataSection[] = []

  if (run_summary.value) {
    const run = run_summary.value
    if (run.citations && run.citations.length > 0) {
      sections.push({
        label: 'Identifiers',
        defaultOpen: true,
        items: [
          {
            icon: 'i-lucide-book',
            title: 'Citation',
            values: run.citations.filter(c => c.label).map(c => ({ label: c.label, url: c.uri || undefined }))
          }
        ]
      })
    }
    // Encodes for biology
    if (run.encodes && run.encodes.length > 0) {
      sections.push({
        label: 'Biology',
        defaultOpen: true,
        items: [
          {
            icon: 'i-lucide-dna',
            title: 'Taxonomy/Biology',
            values: run.encodes.filter(e => e.label).map(e => ({ label: e.label, url: e.uri || undefined }))
          }
        ]
      })
    }
    const simItems: MetadataItem[] = []
    if (run.simulator) {
      simItems.push({
        icon: 'i-lucide-cpu',
        title: 'Simulator',
        values: [{ label: `${run.simulator.name} v${run.simulator.version}` }]
      })
    }
    if (run.modelFormats && run.modelFormats.length > 0) {
      simItems.push({
        icon: 'i-lucide-file-code',
        title: 'Model Formats',
        values: [{ label: run.modelFormats.join(', ') }]
      })
    }
    if (simItems.length > 0) {
      sections.push({ label: 'Simulation', defaultOpen: true, items: simItems })
    }
  }
  return sections
}

const detailedInfoSections = computed(() => getMetadataSections())
</script>

<template>
  <section class="w-full min-h-[calc(100vh-var(--ui-header-height))] p-6 max-w-(--ui-container) mx-auto flex flex-col gap-4" :class="{'items-center justify-center': !all_data_fetched || error_encountered, 'items-start justify-start': !error_encountered}">
    <template v-if="error_encountered">
      <ErrorPage v-if="error_encountered" message="An error occurred while fetching project details" :error="error_encountered" @refresh="fetch_run" />
    </template>
    <template v-else>
      <template v-if="!all_data_fetched">
        <Loading class="mx-auto" v-if="!all_data_fetched && !error_encountered" message="Fetching project details..." />
      </template>
      <template v-else>
        <UBreadcrumb class="mx-auto" :items="breadcrumbs"></UBreadcrumb>

        <div class="page_header relative overflow-hidden w-full p-8 bg-primary-500 text-white flex flex-col items-center justify-center gap-2 rounded-lg">
          <h1 class="text-xl font-bold">{{normalize_text(run_summary!.name)}}</h1>
          <template v-if="descriptionContent.html">
            <div class="text-center" :class="{'line-clamp-3': descriptionContent.isLong && !isDescriptionExpanded}" v-dompurify-html="descriptionContent.html"></div>
            <UButton v-if="descriptionContent.isLong" @click="isDescriptionExpanded = !isDescriptionExpanded" class="mt-2 bg-white text-primary-500 font-bold hover:bg-gray-100 transition-colors" size="sm">
              {{ isDescriptionExpanded ? 'Show Less' : 'Show More' }}
            </UButton>
          </template>
        </div>

        <div class="w-full flex flex-col gap-4">
          <div class="w-full flex flex-col lg:flex-row items-start gap-4">
            <template v-if="!run_summary?.thumbnails?.[0]">
              <div class="w-full lg:w-187.5 h-48 lg:h-auto bg-neutral-50 flex items-center justify-center p-4 rounded-lg border border-neutral-300">
                <p><em>No thumbnail available</em></p>
              </div>
            </template>
            <template v-else>
              <div class="thumbnail_wrapper group relative rounded-lg overflow-hidden w-full max-w-187.5 mx-auto">
                <div class="overlay w-full h-full absolute opacity-0 group-hover:opacity-100 bg-black/80 flex flex-col items-center justify-center gap-2 p-4 cursor-pointer transition-all" @click="img_zoomed = true">
                  <Icon name="i-lucide-zoom-in" class="text-white size-8" />
                  <p class="text-lg text-white font-bold">Click to expand</p>
                </div>
                <NuxtImg :src="`${runtimeConfig.public.legacy_api_url}/files/${run_summary!.id}/${run_summary!.thumbnails[0]}/download?thumbnail=view`" alt="Simulation Thumbnail Image" class="w-full" />
                <vue-easy-lightbox
                  class="lenis-prevent"
                  :visible="img_zoomed"
                  :imgs="[`${runtimeConfig.public.legacy_api_url}/files/${run_summary!.id}/${run_summary!.thumbnails[0]}/download`]"
                  :index="0"
                  @hide="img_zoomed = false"
                />
              </div>
            </template>

            <div class="metadata_panel lg:sticky lg:top-[calc(1rem+var(--ui-header-height))] w-full lg:w-auto flex-1 flex flex-col p-4 rounded-lg border border-neutral-300 bg-neutral-50">
              <div class="w-full flex items-center justify-start gap-2">
                <Icon name="i-lucide-info" class="size-4.5 text-color" />
                <p class="text-color font-bold">Project Overview</p>
              </div>
              <USeparator color="neutral" class="my-2" />
              <table class="overview_table border-none p-0 m-0 border-collapse w-full">
                <tbody>
                <tr>
                  <td class="py-1"><p class="text-sm font-bold">Project ID:</p></td>
                  <td class="py-1"><span class="text-sm text-color">{{ route.params.id }}</span></td>
                </tr>
                <tr>
                  <td class="py-1"><p class="text-sm font-bold">Created:</p></td>
                  <td class="py-1"><span class="text-sm text-color"><NuxtTime v-if="project_overview?.created" :datetime="project_overview?.created" /><span v-else>N/A</span></span></td>
                </tr>
                <tr>
                  <td class="py-1"><p class="text-sm font-bold">Updated:</p></td>
                  <td class="py-1"><span class="text-sm text-color"><NuxtTime v-if="project_overview?.updated" :datetime="project_overview?.updated" /><span v-else>N/A</span></span></td>
                </tr>
                </tbody>
              </table>

              <template v-for="(section, idx) in detailedInfoSections" :key="idx">
                <USeparator color="neutral" class="my-3" />
                <div class="w-full flex items-center justify-start gap-2 mb-2">
                  <Icon v-if="section.label === 'Identifiers'" name="i-lucide-book" class="size-4.5 text-color" />
                  <Icon v-else-if="section.label === 'Biology'" name="i-lucide-dna" class="size-4.5 text-color" />
                  <Icon v-else-if="section.label === 'Simulation'" name="i-lucide-cpu" class="size-4.5 text-color" />
                  <p class="text-sm text-color font-bold">{{ section.label }}</p>
                </div>
                <table class="border-none p-0 m-0 border-collapse w-full">
                  <tbody>
                    <tr v-for="(info, i) in section.items" :key="i" class="border-b last:border-0 border-neutral-100">
                      <td class="py-1.5 w-1/3 align-top"><p class="text-xs font-bold text-gray-700">{{ info.title }}:</p></td>
                      <td class="py-1.5 align-top break-all">
                        <ul v-if="info.values.length > 1" class="list-disc pl-4 m-0">
                          <li v-for="(val, j) in info.values" :key="j" class="text-xs text-gray-800">
                            <a v-if="val.url" :href="val.url" target="_blank" class="text-primary-600 hover:underline">{{ val.label }}</a>
                            <span v-else>{{ val.label }}</span>
                          </li>
                        </ul>
                        <div v-else>
                          <a v-if="info.values[0]?.url" :href="info.values[0]?.url" target="_blank" class="text-xs text-primary-600 hover:underline">{{ info.values[0]?.label }}</a>
                          <span v-else class="text-xs text-gray-800">{{ info.values[0]?.label }}</span>
                        </div>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </template>

              <div class="mt-4 pt-4 border-t border-neutral-200">
                <UButton
                  :to="`https://reproducibilityportal.org/model/${run_summary!.id}`"
                  target="_blank"
                  color="primary"
                  variant="outline"
                  icon="i-lucide-external-link"
                  label="View in Reproducibility Portal"
                  block
                />
                <UButton
                  :to="`/runs/${run_summary!.id}`"
                  color="primary"
                  class="mt-2"
                  variant="solid"
                  size="lg"
                  icon="i-lucide-activity"
                  :label="`View Full Simulation Details`"
                  block
                />
              </div>
            </div>
          </div>

          <USeparator />

          <!-- Figures Section -->
          <div class="flex flex-col flex-1">
            <p class="text-lg mb-4"><strong>Figures</strong></p>

            <div v-if="allVisualizations.length > 0" class="flex flex-col gap-8">
              <div v-for="vis in allVisualizations" :key="vis.name" class="figure-container border border-primary-200 rounded-lg bg-white overflow-hidden shadow-sm">
                <div class="bg-primary-500 text-white p-3 font-semibold">
                  {{ vis.name }}
                </div>
                <div class="p-4">
                  <div v-if="vis._isLoading" class="w-full h-[400px] flex flex-col items-center justify-center gap-4 text-gray-500">
                    <UIcon name="i-lucide-loader-2" class="w-8 h-8 animate-spin" />
                    <p>Loading visualization data...</p>
                  </div>
                  <div v-else-if="vis._error" class="w-full h-[400px] flex flex-col items-center justify-center gap-4 text-red-500">
                    <UIcon name="i-lucide-alert-circle" class="w-8 h-8" />
                    <p>{{ vis._error }}</p>
                  </div>
                  <template v-else>
                    <div v-if="vis.renderer === 'Vega'" class="relative w-full max-h-[400px] overflow-hidden flex justify-center items-center">
                      <VegaVisualization
                        :spec="vis.vegaSpec"
                        :name="vis.name"
                      />
                    </div>
                    <div v-else-if="vis.renderer === 'Plotly'" class="relative w-full h-[400px]">
                      <PlotlyChart
                        :data="vis.plotlyDataLayout?.data || []"
                        :layout="Object.assign({}, plot_layout, vis.plotlyDataLayout?.layout || {})"
                        :config="plot_config"
                        class="absolute inset-0"
                      />
                    </div>
                  </template>
                </div>
              </div>
            </div>
            <div v-else class="p-8 text-center text-gray-500 bg-gray-50 border rounded">
              <p>No figures available for this project.</p>
            </div>
          </div>

          <USeparator />

          <!-- Files & Outputs Section -->
          <div class="w-full flex flex-col gap-2">
            <p class="text-lg mb-2"><strong>Files & Outputs</strong></p>
            <FilesOutputsTable :summary="run_summary" :files="run_files" :run-id="run_summary?.id" />
          </div>
        </div>
      </template>
    </template>
  </section>
</template>
<style scoped lang="scss">
.overview_table {
  tr {
    td:first-child {
      white-space: nowrap;
    }
  }
}
</style>
