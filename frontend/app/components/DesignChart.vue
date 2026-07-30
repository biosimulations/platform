<template>
  <div class="design-chart-container w-full flex flex-col gap-6">
    <div class="configure-panel mt-4 border border-neutral-200 rounded-lg p-5 bg-white shadow-sm flex flex-col gap-4">
      <div class="flex items-center justify-between">
        <h3 class="text-lg font-bold">Configure {{ props.visualization.name }}</h3>
        <UButton color="primary" class="cursor-pointer" label="Generate Plot" :loading="loading" @click="generatePlot" />
      </div>

      <div class="settings-panel flex flex-col md:flex-row items-end justify-between gap-4">
        <div class="w-max whitespace-nowrap flex flex-row items-end gap-4">
          <UFormField label="X-Axis Scale">
            <USelectMenu class="min-w-35" v-model="xAxisType" :items="['linear', 'log']" />
          </UFormField>

          <UFormField label="Y-Axis Scale">
            <USelectMenu class="min-w-35" v-model="yAxisType" :items="['linear', 'log']" />
          </UFormField>
        </div>

        <UFormField v-if="props.visualization._type === 'Line2DVisualization'" class="w-max" :ui="{label: 'whitespace-nowrap'}" label="Trace Mode">
          <USelectMenu class="min-w-35" v-model="traceMode" :items="[{label: 'Lines', value: 'lines'}, {label: 'Markers', value: 'markers'}, {label: 'Lines & Markers', value: 'lines+markers'}]" label-key="label" value-key="value" />
        </UFormField>
      </div>

      <UCard class="flex-1">
        <template #header><p><strong>Curve Configuration</strong></p></template>

        <!-- Error Display -->
        <div v-if="error" class="p-4 mb-4 bg-red-50 text-red-600 rounded">
          {{ error }}
        </div>

        <div v-for="(curve, index) in curves" :key="index" class="curve-config flex flex-col gap-4 p-3 bg-neutral-50/75 border border-neutral-200 rounded relative group" :class="{'mt-2': index > 0}">
          <div class="flex-1 w-full flex items-center justify-between gap-4">
            <p><strong>Curve {{index + 1}}</strong></p>
            <UButton
              v-if="curves.length > 1"
              icon="i-lucide-trash"
              color="error"
              variant="ghost"
              size="xs"
              class="cursor-pointer"
              @click="removeCurve(index)"
            />
          </div>
          <div class="w-max flex items-center gap-4">
            <UFormField class="w-full md:flex-1" :label="props.visualization._type === 'Histogram1DVisualization' ? `Data ${index + 1}` : `Curve ${index + 1} - X Data`">
              <USelectMenu
                v-model="curve.xData"
                :items="(datasetOptions as any[])"
                multiple
                searchable
                :content="{
                      align: 'start',
                      sideOffset: 4,
                      collisionPadding: 16
                    }"
                label-key="label"
                value-key="uri"
                placeholder="Select X Data"
              />
            </UFormField>
            <UFormField v-if="props.visualization._type !== 'Histogram1DVisualization'" class="w-full md:flex-1" :label="`Curve ${index + 1} - Y Data`">
              <USelectMenu
                v-model="curve.yData"
                :items="(datasetOptions as any[])"
                multiple
                searchable
                :content="{
                      align: 'start',
                      sideOffset: 4,
                      collisionPadding: 16
                    }"
                label-key="label"
                value-key="uri"
                placeholder="Select Y Data"
              />
            </UFormField>
          </div>
        </div>

        <template #footer>
          <UButton v-if="props.visualization._type !== 'Heatmap2DVisualization'" class="w-max self-end cursor-pointer" icon="i-lucide-plus" size="sm" :label="props.visualization._type === 'Histogram1DVisualization' ? 'Add Data' : 'Add Curve'" @click="addCurve" />
        </template>
      </UCard>
    </div>

    <!-- Chart Display -->
    <div v-if="plotlyDataLayout" class="chart-wrapper border rounded bg-white p-4">
      <div class="relative w-full aspect-video min-h-[500px]">
        <PlotlyChart
          :data="plotlyDataLayout.data"
          :layout="Object.assign({}, defaultLayout, plotlyDataLayout.layout)"
          :config="defaultConfig"
          class="absolute inset-0"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import type { UserDesignedVisualization } from '~/models/visualizations';
import { flattenTaskResults, getRepeatedTaskTraceLabel } from '~/functions/utils';

const props = defineProps<{
  visualization: UserDesignedVisualization;
  defaultLayout: any;
  defaultConfig: any;
}>();

const config = useRuntimeConfig();
const api_url = config.public.legacy_api_url;

const datasetOptions = computed(() => {
  return Object.values(props.visualization.uriSedDataSetMap || {});
});

const curves = ref<{ xData: any[]; yData: any[] }[]>([{ xData: [], yData: [] }]);

const xAxisType = ref('linear');
const yAxisType = ref('linear');
const traceMode = ref('lines'); // lines, markers, lines+markers

const loading = ref(false);
const error = ref('');
const plotlyDataLayout = ref<any>(null);

function addCurve() {
  curves.value.push({ xData: [], yData: [] });
}

function removeCurve(index: number) {
  curves.value.splice(index, 1);
}

// Reset form when switching between Line / Heatmap / Histogram
watch(() => props.visualization._type, () => {
  curves.value = [{ xData: [], yData: [] }];
  plotlyDataLayout.value = null;
  error.value = '';
});

async function fetchResults(uris: string[]) {
  // Group URIs by report (location/outputId)
  const reportMap: Record<string, Set<string>> = {};
  uris.forEach((uri) => {
    // uri is format: location/outputId/dataSetId
    const parts = uri.split('/');
    const dataSetId = parts.pop();
    const reportId = parts.join('/');
    if (!reportMap[reportId]) reportMap[reportId] = new Set();
    reportMap[reportId].add(dataSetId!);
  });

  const resultsData: any = {};

  // Fetch for each report
  const promises = Object.keys(reportMap).map(async (reportId) => {
    try {
      const param = encodeURIComponent(reportId);
      const url = `${api_url}/results/${props.visualization.simulationRunId}/${param}?includeData=true`;
      const res: any = await $fetch(url);

      const neededIds = reportMap[reportId];
      if (res && res.data) {
        res.data.forEach((datum: any) => {
          if (neededIds && neededIds.has(datum.id)) {
            const uri = `${reportId}/${datum.id}`;
            resultsData[uri] = datum.values;
          }
        });
      }
    } catch (err) {
      console.error(`Error fetching data for ${reportId}`, err);
    }
  });

  await Promise.all(promises);
  return resultsData;
}

async function generatePlot() {
  error.value = '';
  loading.value = true;
  plotlyDataLayout.value = null;

  try {
    // Collect all required URIs
    const allUris = new Set<string>();
    curves.value.forEach((curve) => {
      // In Nuxt UI, when using multiple select and value-key, v-model holds array of values or array of objects depending on version.
      // Let's normalize it to strings just in case
      const xUris = curve.xData.map(v => typeof v === 'object' ? v.uri : v);
      const yUris = curve.yData.map(v => typeof v === 'object' ? v.uri : v);
      xUris.forEach(uri => allUris.add(uri));
      yUris.forEach(uri => allUris.add(uri));
    });

    if (allUris.size === 0) {
      error.value = 'Please select at least one dataset.';
      loading.value = false;
      return;
    }

    const fetchedData = await fetchResults(Array.from(allUris));

    // Now build Plotly traces
    const traces: any[] = [];
    const xAxisTitlesSet = new Set<string>();
    const yAxisTitlesSet = new Set<string>();

    if (props.visualization._type === 'Histogram1DVisualization') {
      // Histogram logic
      curves.value.forEach((curve) => {
        const xUris = curve.xData.map(v => typeof v === 'object' ? v.uri : v);
        xUris.forEach((xUri) => {
          const xData = fetchedData[xUri];
          if (xData) {
            const xLabel = props.visualization.uriSedDataSetMap?.[xUri]?.label || xUri;
            xAxisTitlesSet.add(xLabel);

            let flatData: any[] = [];
            if (Array.isArray(xData) && xData.length > 0 && Array.isArray(xData[0])) {
               flatData = flattenTaskResults([xData]).data[0]?.[0] || [];
            } else {
               flatData = Array.isArray(xData) ? xData : [xData];
            }

            traces.push({
              name: xLabel,
              x: flatData,
              type: 'histogram'
            });
          }
        });
      });
    } else if (props.visualization._type === 'Heatmap2DVisualization') {
      // Heatmap logic
      const xDataUris = curves.value[0]?.xData.map(v => typeof v === 'object' ? v.uri : v) || [];
      const yDataUris = curves.value[0]?.yData.map(v => typeof v === 'object' ? v.uri : v) || [];

      if (xDataUris.length > 0 && yDataUris.length > 0) {
        const xValues = fetchedData[xDataUris[0]!] || [];
        const yValues = fetchedData[yDataUris[0]!] || [];

        let zValues: any[] = [];
        if (xValues.length > 0 && Array.isArray(xValues[0])) {
           zValues = flattenTaskResults([xValues]).data[0] || [];
        } else {
           zValues = xValues;
        }

        traces.push({
          type: 'heatmap',
          x: xValues,
          y: yValues,
          z: zValues // This is a simplification. Actual heatmap logic requires properly gridded Z data.
        });
        xAxisTitlesSet.add(props.visualization.uriSedDataSetMap?.[xDataUris[0]]?.label || xDataUris[0]);
        yAxisTitlesSet.add(props.visualization.uriSedDataSetMap?.[yDataUris[0]]?.label || yDataUris[0]);
      }
    } else {
      // Line logic
      curves.value.forEach((curve) => {
        const xUris = curve.xData.map(v => typeof v === 'object' ? v.uri : v);
        const yUris = curve.yData.map(v => typeof v === 'object' ? v.uri : v);

        xUris.forEach((xUri) => {
          yUris.forEach((yUri) => {
            const xData = fetchedData[xUri];
            const yData = fetchedData[yUri];

            if (xData && yData) {
              const xLabel = props.visualization.uriSedDataSetMap?.[xUri]?.label || xUri;
              const yLabel = props.visualization.uriSedDataSetMap?.[yUri]?.label || yUri;
              xAxisTitlesSet.add(xLabel);
              yAxisTitlesSet.add(yLabel);

              const flatData = flattenTaskResults([xData, yData]);
              for (let iTrace = 0; iTrace < (flatData.data[0]?.length || 0); iTrace++) {
                const name = `${yLabel} vs ${xLabel}`
                  + ((flatData.data[0]?.length || 0) > 1 ? ` (${getRepeatedTaskTraceLabel(iTrace, flatData.outerShape)})` : '');

                traces.push({
                  name: name,
                  x: flatData.data[0]![iTrace],
                  y: flatData.data[1]![iTrace],
                  type: 'scatter',
                  mode: traceMode.value === 'lines+markers' ? 'lines+markers' : traceMode.value
                });
              }
            }
          });
        });
      });
    }

    const xAxisTitlesArr = Array.from(xAxisTitlesSet);
    const yAxisTitlesArr = Array.from(yAxisTitlesSet);

    plotlyDataLayout.value = {
      data: traces,
      layout: {
        xaxis1: {
          anchor: 'x1',
          title: xAxisTitlesArr.length > 1 ? 'Multiple' : xAxisTitlesArr[0],
          type: xAxisType.value
        },
        yaxis1: {
          anchor: 'y1',
          title: yAxisTitlesArr.length > 1 ? 'Multiple' : yAxisTitlesArr[0],
          type: yAxisType.value
        },
        showlegend: traces.length > 1
      }
    };
  } catch (err: any) {
    console.error(err);
    error.value = 'Failed to generate plot. See console for details.';
  } finally {
    loading.value = false;
  }
}
</script>
