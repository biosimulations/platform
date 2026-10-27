<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue';
import { debounce } from 'throttle-debounce';
import { sanitizeVegaSpec } from '~/utils/vega';

const props = defineProps<{
  spec: any; // Raw JSON Vega Configuration
  name?: string;
}>();

const vegaContainer = ref<HTMLElement | null>(null);
const loading = ref(true);
const error = ref('');

let vegaView: any = null;
let builtInConsoleWarn: any = null;
let resizeObserver: ResizeObserver | null = null;

const renderDebounce = debounce(200, () => {
  renderVega();
});

async function renderVega() {
  if (!vegaContainer.value || !props.spec) {
    error.value = 'The visualization could not be loaded.';
    loading.value = false;
    return;
  }

  if (vegaView) {
    vegaView.finalize?.();
    vegaView = null;
  }

  const parent = vegaContainer.value.parentElement;
  if (!parent) return;

  const rect = parent.getBoundingClientRect();
  if (!rect || rect.width === 0 || rect.height === 0) return;

  const cleanSpec = sanitizeVegaSpec(props.spec);

  const options: Record<string, any> = {
    actions: {
      export: { svg: true, png: true },
      source: false,
      compiled: false,
      editor: true,
    },
    padding: 10,
  };

  // Only constrain width if container is narrower than designed spec width
  if (rect.width > 0 && cleanSpec.width && rect.width < cleanSpec.width) {
    options.width = Math.max(Math.floor(rect.width) - 24, 280);
  }
  // DO NOT force options.height: letting Vega compute its own natural height
  // breaks the infinite ResizeObserver height feedback loop.

  // Extract static target URLs for error tracking injection
  const dataUrls: string[] = [];
  cleanSpec.data?.forEach((d: any) => {
    if (d?.url) dataUrls.push(d.url);
  });

  // Porting console intercept log mechanics safely
  console.warn = function (...args: any[]): void {
    if (
      args.length === 4 && args[0] === 'WARN' && args[1] === 'Loading failed'
      && dataUrls.includes(args[2]) && args[3]?.constructor?.name === 'Error'
    ) {
      error.value = 'The data for the visualization could not be loaded.';
    } else if (
      typeof args[0] === 'string'
      && args[0].includes('The input spec uses Vega')
    ) {
      // Suppress harmless schema version mismatch notice (v5 spec running on Vega v6 runtime)
      return;
    } else if (builtInConsoleWarn) {
      builtInConsoleWarn(...args);
    }
  };

  try {
    // Dynamic execution to safely support Nuxt SSR/SSG compilation pathways
    const { default: vegaEmbed } = await import('vega-embed');
    const result = await vegaEmbed(vegaContainer.value, cleanSpec, options);
    vegaView = result?.view;
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

  let lastWidth = 0;
  // Use ResizeObserver instead of third party wrapper modules
  if (vegaContainer.value?.parentElement) {
    resizeObserver = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      const currentWidth = Math.round(entry.contentRect.width);
      // ONLY re-render when WIDTH changes significantly (e.g. window resized, sidebar toggle)
      // Completely ignore height changes to prevent infinite ResizeObserver feedback loop!
      if (currentWidth > 0 && Math.abs(currentWidth - lastWidth) > 15) {
        lastWidth = currentWidth;
        renderDebounce();
      }
    });
    resizeObserver.observe(vegaContainer.value.parentElement);
  }
});

onBeforeUnmount(() => {
  renderDebounce.cancel();
  if (builtInConsoleWarn) console.warn = builtInConsoleWarn;
  if (resizeObserver) resizeObserver.disconnect();
  if (vegaView) {
    vegaView.finalize?.();
    vegaView = null;
  }
});
</script>

<template>
  <div class="w-full relative min-h-[400px] flex flex-col items-center justify-center overflow-x-auto">
    <div v-if="loading" class="absolute inset-0 flex items-center justify-center bg-white/80 z-10">
      <p class="text-sm text-neutral-500">Compiling Vega Blueprint...</p>
    </div>

    <div v-if="!loading && error" class="p-4 border border-red-200 bg-red-50 text-red-700 rounded-lg text-sm w-full">
      <p>Sorry! {{ error }} Please try refreshing.</p>
    </div>

    <!-- Container wrapper element matching original template spec -->
    <div
      ref="vegaContainer"
      :style="{ display: error ? 'none' : 'block', visibility: loading ? 'hidden' : 'visible' }"
      class="w-full flex justify-center items-center overflow-x-auto"
    ></div>
  </div>
</template>
