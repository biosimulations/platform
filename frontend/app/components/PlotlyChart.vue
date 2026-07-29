<template>
  <div ref="chartContainer" class="plotly-container"></div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'

const props = defineProps({
  data: { type: Array, required: true },
  layout: { type: Object, default: () => ({}) },
  config: { type: Object, default: () => ({ responsive: true }) }
})

const chartContainer = ref(null)

onMounted(() => {
  if (!chartContainer.value) return;

  const renderChart = () => {
    if (window.Plotly) {
      window.Plotly.newPlot(chartContainer.value, props.data, props.layout, props.config);
    }
  };

  if (window.Plotly) {
    renderChart();
  } else {
    const script = document.createElement('script');
    script.src = 'https://cdn.plot.ly/plotly-2.34.0.min.js';
    script.onload = renderChart;
    document.head.appendChild(script);
  }
})

watch([() => props.data, () => props.layout, () => props.config], ([newData, newLayout, newConfig]) => {
  if (window.Plotly && chartContainer.value) {
    window.Plotly.react(chartContainer.value, newData, newLayout, newConfig)
  }
}, { deep: true })
</script>

<style scoped>
.plotly-container {
  width: 100%;
  height: 100%;
}
</style>
