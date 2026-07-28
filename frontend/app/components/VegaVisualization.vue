<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue';
import { debounce } from 'throttle-debounce';

const props = defineProps<{
  spec: any; // Raw JSON Vega Configuration
  name?: string;
}>();

const vegaContainer = ref<HTMLElement | null>(null);
const loading = ref(true);
const error = ref('');

let builtInConsoleWarn: any = null;
let resizeObserver: ResizeObserver | null = null;

const renderDebounce = debounce(200, false, () => {
  renderVega();
});

async function renderVega() {
  if (!vegaContainer.value || !props.spec) {
    error.value = 'The visualization could not be loaded.';
    loading.value = false;
    return;
  }

  const parent = vegaContainer.value.parentElement;
  if (!parent) return;

  const rect = parent.getBoundingClientRect();
  if (!rect || rect.width === 0 || rect.height === 0) return;

  const options = {
    width: Math.max(rect.width, 10),
    height: Math.max(rect.height, 10),
    padding: 0,
  };

  // Extract static target URLs for error tracking injection
  const dataUrls: string[] = [];
  props.spec.data?.forEach((d: any) => { if (d?.url) dataUrls.push(d.url); });

  // Porting console intercept log mechanics safely
  console.warn = function (...args: any[]): void {
    if (
      args.length === 4 && args[0] === 'WARN' && args[1] === 'Loading failed' &&
      dataUrls.includes(args[2]) && args[3]?.constructor?.name === 'Error'
    ) {
      error.value = 'The data for the visualization could not be loaded.';
    } else if (builtInConsoleWarn) {
      builtInConsoleWarn(...args);
    }
  };

  try {
    // Dynamic execution to safely support Nuxt SSR/SSG compilation pathways
    const { default: vegaEmbed } = await import('vega-embed');
    await vegaEmbed(vegaContainer.value, props.spec, options);
    loading.value = false;
  } catch (err: any) {
    console.error(err);
    loading.value = false;
    error.value = `The visualization is invalid: ${err.message || err}.`;
  }
}

watch(() => props.spec, () => {
  loading.value = true;
  error.value = '';
  nextTick(() => renderVega());
}, { deep: true, immediate: true });

onMounted(() => {
  builtInConsoleWarn = console.warn;

  // Use ResizeObserver instead of third party wrapper modules
  if (vegaContainer.value?.parentElement) {
    resizeObserver = new ResizeObserver(() => renderDebounce());
    resizeObserver.observe(vegaContainer.value.parentElement);
  }
});

onBeforeUnmount(() => {
  renderDebounce.cancel();
  if (builtInConsoleWarn) console.warn = builtInConsoleWarn;
  if (resizeObserver) resizeObserver.disconnect();
});
</script>

<template>
  <div class="w-full h-full relative min-h-[400px] flex flex-col">
    <div v-if="loading" class="absolute inset-0 flex items-center justify-center bg-white/80 z-10">
      <p class="text-sm text-neutral-500">Compiling Vega Blueprint...</p>
    </div>

    <div v-if="!loading && error" class="p-4 border border-red-200 bg-red-50 text-red-700 rounded-lg text-sm">
      <p>Sorry! {{ error }} Please try refreshing.</p>
    </div>

    <!-- Container wrapper element matching original template spec -->
    <div
      ref="vegaContainer"
      :style="{ display: loading || error ? 'none' : 'block' }"
      class="w-full h-full flex-1"
    ></div>
  </div>
</template>
