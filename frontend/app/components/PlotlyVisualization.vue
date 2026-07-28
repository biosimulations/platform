<script setup lang="ts">
import { ref, watch } from 'vue';

const props = withDefaults(defineProps<{
  plotTitle?: string;
  projectTitle?: string;
  customizedAxis?: boolean;
  sliderEnabled?: boolean;
  dataLayout?: any;
}>(), {
  plotTitle: '',
  projectTitle: '',
  customizedAxis: false,
  sliderEnabled: false
});

const loading = ref(false);
const errors = ref<string[]>([]);
const plotData = ref<any[] | undefined>(undefined);
const plotLayout = ref<any | undefined>(undefined);
const currentSliderEnabled = ref(props.sliderEnabled);

const plotConfig = ref({
  scrollZoom: true,
  editable: false,
  toImageButtonOptions: {
    format: 'svg',
    height: 500,
    width: 700,
    scale: 1,
    filename: 'plot'
  },
  displayLogo: false,
  showEditInChartStudio: true,
  plotlyServerURL: 'https://chart-studio.plotly.com',
  responsive: true,
});

function getLegendLayout() {
  return {
    x: 4.5, y: 1.5,
    orientation: 'v',
    traceorder: 'normal',
    font: { family: 'sans-serif', size: 9, color: '#000' },
    display: 'grid',
    bgcolor: '#DCDCDC',
    bordercolor: '#FFFFFF',
    borderwidth: 1,
  };
}

function getAxisLayout(enableSlider = false) {
  const axisLayout: any = {
    tickfont: { size: 14, color: 'black' },
    font: { family: 'Roboto, sans-serif', size: 18, color: '#7f7f7f' },
  };
  if (enableSlider) {
    axisLayout.rangeslider = { autorange: true, bordercolor: '#ff7b00', borderwidth: 1, thickness: 0.15 };
  }
  return axisLayout;
}

// Replicates the Angular Input Setter logic
watch(() => props.dataLayout, (value) => {
  if (value == null) {
    loading.value = true;
    errors.value = [];
  } else if (value.data && value.layout) {
    loading.value = false;
    plotData.value = value.data;
    plotLayout.value = value.layout;
    plotConfig.value.toImageButtonOptions.filename = `${props.projectTitle}_${props.plotTitle}`;
    errors.value = [];

    // Set Layout Additions
    plotLayout.value.autosize = true;
    plotLayout.value.legend = getLegendLayout();
    if (props.customizedAxis) {
      plotLayout.value.xaxis = getAxisLayout(currentSliderEnabled.value);
    }
  } else {
    loading.value = false;
    errors.value = value?.dataErrors || ['Invalid chart layout data structure.'];
  }
}, { immediate: true, deep: true });

function toggleRangeSlider() {
  currentSliderEnabled.value = !currentSliderEnabled.value;
  if (plotLayout.value?.xaxis?.rangeslider) {
    plotLayout.value.xaxis.rangeslider.autorange = currentSliderEnabled.value;
    plotLayout.value = { ...plotLayout.value }; // force deep layout refresh
  }
}
</script>

<template>
  <div class="flex flex-col gap-4 w-full">
    <div v-if="plotLayout?.xaxis?.rangeslider" class="flex items-center gap-4">
      <label class="flex items-center gap-2 cursor-pointer text-sm">
        <input type="radio" :value="true" :checked="currentSliderEnabled" @change="toggleRangeSlider" />
        <span>Enable Slider</span>
      </label>
      <label class="flex items-center gap-2 cursor-pointer text-sm">
        <input type="radio" :value="false" :checked="!currentSliderEnabled" @change="toggleRangeSlider" />
        <span>Disable Slider</span>
      </label>
    </div>

    <div class="relative w-full min-h-[400px] flex-1">
      <div v-if="loading" class="absolute inset-0 flex items-center justify-center bg-white/80 z-10">
        <p class="text-sm text-neutral-500">Loading Plotly Visualization...</p>
      </div>

      <div v-if="errors.length > 0" class="p-4 border border-red-200 bg-red-50 text-red-700 rounded-lg text-sm">
        <p class="font-bold mb-1">The plot could not be loaded.</p>
        <ul class="list-disc pl-5"><li v-for="(err, idx) in errors" :key="idx">{{ err }}</li></ul>
      </div>

      <!-- Nuxt automatically resolves PlotlyChart to PlotlyChart.client.vue -->
      <PlotlyChart
        v-if="!loading && errors.length === 0"
        :data="plotData"
        :layout="plotLayout"
        :config="plotConfig"
        class="w-full h-full absolute inset-0"
      />
    </div>
  </div>
</template>
