<script setup lang="ts">
// Import your types/interfaces here
interface Visualization {
  renderer: 'Vega' | 'Plotly';
  name: string;
  vegaSpec?: any;
  plotlyDataLayout?: any;
}

defineProps<{
  visualization?: Visualization | null;
  plotTitle?: string;
  projectTitle?: string;
  customAxis?: boolean;
}>();
</script>

<template>
  <div class="w-full min-h-[400px] flex flex-col justify-stretch">
    <template v-if="visualization">
      <!-- Case: Vega Renderer -->
      <VegaVisualization
        v-if="visualization.renderer === 'Vega'"
        :spec="visualization.vegaSpec"
        :name="`${visualization.name}_vega`"
      />

      <!-- Case: Plotly Renderer -->
      <PlotlyVisualization
        v-else-if="visualization.renderer === 'Plotly'"
        :data-layout="visualization.plotlyDataLayout"
        :plot-title="plotTitle || ''"
        :project-title="projectTitle || ''"
        :customized-axis="customAxis"
      />
    </template>

    <template v-else>
      <div class="flex-1 flex items-center justify-center p-6 border border-dashed border-neutral-300 rounded-lg bg-neutral-50">
        <p class="text-neutral-500 font-medium italic">No Project Visualizations Available</p>
      </div>
    </template>
  </div>
</template>
