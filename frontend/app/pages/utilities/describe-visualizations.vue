<script setup lang="ts">
import { ref } from 'vue'
import { useClipboard } from '@vueuse/core'
import type { BreadcrumbItem } from '#ui/components/Breadcrumb.vue'
import type { TabsItem } from '#ui/components/Tabs.vue'

useSeoMeta({
  title: 'Describe Visualizations with Vega - BioSimulations',
  description: 'Design and embed publication-quality Vega visualizations in COMBINE/OMEX archives, or convert Escher, GINsim, and SBGN diagrams using biosimulators-utils.'
})

const breadcrumbs: BreadcrumbItem[] = [
  {
    label: 'Home',
    icon: 'i-lucide-home',
    to: '/'
  },
  {
    label: 'Utilities'
  },
  {
    label: 'Describe Visualizations'
  }
]

const { copy, copied } = useClipboard()
const toast = useToast()

const activeCliTab = ref('install')

const cliTabs: TabsItem[] = [
  { label: 'Install', value: 'install', icon: 'i-lucide-download' },
  { label: 'Escher', value: 'escher', icon: 'i-lucide-git-fork' },
  { label: 'GINsim', value: 'ginsim', icon: 'i-lucide-network' },
  { label: 'SBGN', value: 'sbgn', icon: 'i-lucide-workflow' },
  { label: 'Bundle', value: 'bundle', icon: 'i-lucide-folder-archive' }
]

const cliSnippets: Record<string, { cmd: string, description: string }> = {
  install: {
    cmd: 'pip install biosimulators-utils[visualizations]',
    description: 'Install the BioSimulators utilities package with diagram visualization and conversion dependencies.'
  },
  escher: {
    cmd: 'biosimulators-utils convert-escher \\\n  --input-file path/to/metabolic_map.json \\\n  --output-file path/to/map.vg.json \\\n  --sedml-report-id BIOMD0000000010/report',
    description: 'Convert an Escher metabolic map into a Vega specification bound to SED-ML flux distributions.'
  },
  ginsim: {
    cmd: 'biosimulators-utils convert-ginsim \\\n  --input-file path/to/regulatory_network.ginml \\\n  --output-file path/to/network.vg.json',
    description: 'Transform GINsim qualitative logical networks into an interactive Vega graph representation.'
  },
  sbgn: {
    cmd: 'biosimulators-utils convert-sbgn \\\n  --input-file path/to/process_diagram.sbgn \\\n  --output-file path/to/diagram.vg.json',
    description: 'Convert Systems Biology Graphical Notation (SBGN-ML) process descriptions into dynamic Vega glyphs.'
  },
  bundle: {
    cmd: 'biosimulators-utils bundle-visualization \\\n  --archive-file path/to/model.omex \\\n  --vega-file path/to/visualization.vg.json \\\n  --manifest-location simulation_figure.vg.json',
    description: 'Register and package your Vega visualization into the COMBINE/OMEX archive manifest.'
  }
}

function copyCode(text: string) {
  copy(text)
  toast.add({
    title: 'Command copied',
    description: 'Code snippet copied to clipboard.',
    color: 'success',
    icon: 'i-lucide-check'
  })
}
</script>

<template>
  <div class="min-h-screen bg-neutral-50 dark:bg-neutral-950 py-8 px-4 sm:px-6 lg:px-8">
    <div class="max-w-7xl mx-auto space-y-8">
      <!-- Breadcrumbs & Header -->
      <div>
        <UBreadcrumb :items="breadcrumbs" class="mb-3">
          <template #separator>
            <span class="mx-1 text-neutral-400 dark:text-neutral-600">/</span>
          </template>
        </UBreadcrumb>

        <div class="flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
          <div>
            <h1 class="text-3xl font-bold tracking-tight text-neutral-900 flex items-center gap-3">
              <UIcon name="i-lucide-bar-chart-3" class="size-8 text-primary" />
              Describe Visualizations with Vega
            </h1>
            <p class="mt-1 text-sm text-neutral-600 max-w-3xl">
              Author declarative Vega visualization specifications for simulation results, or automatically convert pathway maps and network diagrams (Escher, GINsim, SBGN) into interactive, publication-ready figures packaged directly within COMBINE/OMEX archives.
            </p>
          </div>

          <div class="flex items-center gap-2">
            <UButton
              to="https://docs.biosimulations.org/users/creating-vega-visualizations/"
              target="_blank"
              color="neutral"
              variant="outline"
              size="sm"
              icon="i-lucide-book-open"
              label="BioSimulations Docs"
            />
            <UButton
              to="https://vega.github.io/vega/"
              target="_blank"
              color="primary"
              variant="soft"
              size="sm"
              icon="i-lucide-external-link"
              label="Vega Specs"
            />
          </div>
        </div>
      </div>

      <!-- Card 1: Vega Standard & SED-ML Data Binding (Full Width) -->
      <UCard class="shadow-sm border border-neutral-200 dark:border-neutral-800">
        <template #header>
          <div class="flex items-start justify-between gap-3">
            <div class="flex items-center gap-3">
              <div class="size-10 rounded-xl bg-primary/10 text-primary flex items-center justify-center shrink-0">
                <UIcon name="i-lucide-sparkles" class="size-5" />
              </div>
              <div>
                <h2 class="text-base sm:text-lg font-semibold text-neutral-900 dark:text-white">
                  Declarative Visualization via Vega
                </h2>
                <p class="text-xs text-neutral-500">
                  Interactive, publication-quality graphics driven directly by simulation datasets
                </p>
              </div>
            </div>
            <UBadge color="primary" variant="subtle" size="md" class="shrink-0">
              JSON Specification
            </UBadge>
          </div>
        </template>

        <div class="space-y-4 text-xs sm:text-sm text-neutral-600 dark:text-neutral-300 leading-relaxed">
          <p>
            BioSimulations standardizes on <strong class="text-neutral-900 dark:text-white font-semibold">Vega</strong>, a declarative visualization grammar in JSON syntax. Rather than bundling static bitmap charts (PNG, JPEG), Vega allows modelers to articulate visual mappings, scales, axes, legends, and multi-view interactive dashboards.
          </p>

          <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-1">
            <div class="p-3.5 rounded-lg bg-neutral-50 dark:bg-neutral-800/60 border border-neutral-200 dark:border-neutral-700/60">
              <div class="flex items-center gap-2 font-semibold text-neutral-900 dark:text-white mb-1 text-xs">
                <UIcon name="i-lucide-database" class="size-4 text-primary shrink-0" />
                SED-ML Data Binding
              </div>
              <p class="text-[11px] text-neutral-500 leading-normal">
                Vega datasets dynamically reference SED-ML report tasks and HDF5 output streams using URI dataset identifiers.
              </p>
            </div>

            <div class="p-3.5 rounded-lg bg-neutral-50 dark:bg-neutral-800/60 border border-neutral-200 dark:border-neutral-700/60">
              <div class="flex items-center gap-2 font-semibold text-neutral-900 dark:text-white mb-1 text-xs">
                <UIcon name="i-lucide-layers" class="size-4 text-primary shrink-0" />
                OMEX Packaging
              </div>
              <p class="text-[11px] text-neutral-500 leading-normal">
                Save as <code class="font-mono text-primary">*.vg.json</code> inside the COMBINE archive and register with format URI <code class="font-mono text-[10px]">combine.specifications/vega</code>.
              </p>
            </div>

            <div class="p-3.5 rounded-lg bg-neutral-50 dark:bg-neutral-800/60 border border-neutral-200 dark:border-neutral-700/60">
              <div class="flex items-center gap-2 font-semibold text-neutral-900 dark:text-white mb-1 text-xs">
                <UIcon name="i-lucide-mouse-pointer-click" class="size-4 text-primary shrink-0" />
                Interactive &amp; Web-Ready
              </div>
              <p class="text-[11px] text-neutral-500 leading-normal">
                Supports pan, zoom, dynamic tooltips, coordinate crosshairs, and multi-series brushing natively in the browser.
              </p>
            </div>
          </div>
        </div>

        <template #footer>
          <div class="flex flex-wrap items-center justify-between gap-3 text-xs pt-1">
            <span class="text-neutral-500 flex items-center gap-1.5 font-mono text-[11px]">
              <UIcon name="i-lucide-file-code" class="size-3.5 text-neutral-400" />
              MIME: application/vnd.vega.v5+json
            </span>
            <div class="flex items-center gap-2">
              <UButton
                to="https://vega.github.io/vega/examples/"
                target="_blank"
                variant="ghost"
                color="neutral"
                size="xs"
                icon="i-lucide-external-link"
                label="Vega Gallery"
              />
              <UButton
                to="https://vega.github.io/vega-lite/"
                target="_blank"
                variant="ghost"
                color="neutral"
                size="xs"
                icon="i-lucide-external-link"
                label="Vega-Lite"
              />
            </div>
          </div>
        </template>
      </UCard>

      <!-- Card 2: Interactive Terminal Code Snippet (biosimulators-utils CLI) (Full Width) -->
      <UCard class="shadow-sm border border-neutral-200 dark:border-neutral-800">
        <template #header>
          <div class="flex items-center justify-between gap-3">
            <div class="flex items-center gap-2.5">
              <div class="size-10 rounded-xl bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 flex items-center justify-center shrink-0">
                <UIcon name="i-lucide-terminal" class="size-5" />
              </div>
              <div>
                <h3 class="text-base sm:text-lg font-semibold text-neutral-900 dark:text-white">
                  biosimulators-utils CLI
                </h3>
                <p class="text-xs text-neutral-500">
                  Automated command-line conversion &amp; COMBINE archive packaging
                </p>
              </div>
            </div>
            <UBadge color="neutral" variant="subtle" size="md" class="shrink-0">
              Python CLI
            </UBadge>
          </div>
        </template>

        <div class="space-y-4">
          <!-- Tabs -->
          <UTabs
            v-model="activeCliTab"
            :items="cliTabs"
            :content="false"
            size="sm"
            color="neutral"
            class="w-full"
          />

          <!-- Context Description -->
          <p class="text-xs text-neutral-600 dark:text-neutral-400 min-h-[24px] leading-relaxed">
            {{ cliSnippets[activeCliTab]?.description }}
          </p>

          <!-- Terminal Window -->
          <div class="relative group rounded-lg bg-neutral-950 text-neutral-200 p-4 font-mono text-xs border border-neutral-800 shadow-inner">
            <div class="flex items-center justify-between pb-2.5 mb-2.5 border-b border-neutral-800 text-neutral-500 text-[11px]">
              <div class="flex items-center gap-1.5">
                <span class="size-2.5 rounded-full bg-rose-500/80" />
                <span class="size-2.5 rounded-full bg-amber-500/80" />
                <span class="size-2.5 rounded-full bg-emerald-500/80" />
                <span class="ml-2 font-sans font-medium text-neutral-400">Terminal - bash</span>
              </div>
              <UButton
                :icon="copied ? 'i-lucide-check' : 'i-lucide-copy'"
                :label="copied ? 'Copied' : 'Copy'"
                color="neutral"
                variant="ghost"
                size="xs"
                class="text-neutral-400 hover:text-white hover:bg-neutral-800"
                @click="copyCode(cliSnippets[activeCliTab]!.cmd)"
              />
            </div>

            <pre class="overflow-x-auto whitespace-pre leading-relaxed text-emerald-400 font-mono"><code>{{ cliSnippets[activeCliTab]?.cmd }}</code></pre>
          </div>
        </div>

        <template #footer>
          <div class="flex items-center justify-between text-xs pt-1">
            <UButton
              to="https://pypi.org/project/biosimulators-utils/"
              target="_blank"
              variant="link"
              color="primary"
              size="xs"
              icon="i-lucide-package"
              label="PyPI package"
              class="p-0 text-xs"
            />
            <UButton
              to="https://github.com/biosimulators/Biosimulators_utils"
              target="_blank"
              variant="link"
              color="neutral"
              size="xs"
              icon="i-lucide-code-2"
              label="Source Code"
              class="p-0 text-xs"
            />
          </div>
        </template>
      </UCard>

      <!-- Section: Diagram & Network Format Converters (3-column grid) -->
      <div>
        <div class="mb-4">
          <h3 class="text-sm font-semibold uppercase tracking-wider text-neutral-500 dark:text-neutral-400">
            Diagram &amp; Network Format Converters
          </h3>
          <p class="text-xs text-neutral-500 dark:text-neutral-400">
            Convert existing biological pathway layouts and regulatory networks directly into Vega figures
          </p>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">

        <!-- Bento Card 3: Escher Metabolic Maps -->
        <UCard class="shadow-sm border border-neutral-200 dark:border-neutral-800 flex flex-col justify-between">
          <template #header>
            <div class="flex items-center justify-between gap-2">
              <div class="flex items-center gap-2.5">
                <div class="size-9 rounded-lg bg-amber-500/10 text-amber-600 dark:text-amber-400 flex items-center justify-center">
                  <UIcon name="i-lucide-git-fork" class="size-5" />
                </div>
                <div>
                  <h3 class="text-sm font-semibold text-neutral-900 dark:text-white">
                    Escher Metabolic Pathways
                  </h3>
                  <p class="text-[11px] text-neutral-500">
                    Flux Balance Analysis (FBA) visualization
                  </p>
                </div>
              </div>
              <UBadge color="warning" variant="subtle" size="md" class="shrink-0">
                SBML-fbc
              </UBadge>
            </div>
          </template>

          <div class="space-y-3 text-xs text-neutral-600 dark:text-neutral-400 leading-relaxed">
            <p>
              <strong class="text-neutral-900 dark:text-white">Escher</strong> is the community standard for building metabolic pathway maps. BioSimulators utilities convert Escher JSON pathway layouts into Vega specifications.
            </p>
            <p>
              Simulation flux distributions calculated via FBA, pFBA, or flux variability analysis (FVA) can be projected onto metabolic reaction arrows (scaling width, color temperature, and hover labels).
            </p>

            <div class="p-2.5 rounded-lg bg-neutral-50 dark:bg-neutral-800/60 border border-neutral-100 dark:border-neutral-800 space-y-1">
              <span class="block text-[10px] uppercase font-semibold text-neutral-400">Supported Formats:</span>
              <div class="flex items-center gap-1.5 flex-wrap font-mono text-[11px]">
                <span class="px-1.5 py-0.5 rounded bg-neutral-200/70 dark:bg-neutral-700/60 text-neutral-800 dark:text-neutral-200">.json (Escher)</span>
                <UIcon name="i-lucide-arrow-right" class="size-3 text-neutral-400" />
                <span class="px-1.5 py-0.5 rounded bg-primary/10 text-primary font-semibold">.vg.json (Vega)</span>
              </div>
            </div>
          </div>

          <template #footer>
            <div class="flex items-center justify-between text-xs pt-1">
              <UButton
                to="https://escher.github.io/"
                target="_blank"
                variant="link"
                color="primary"
                size="xs"
                icon="i-lucide-external-link"
                label="Escher Builder"
                class="p-0 text-[11px]"
              />
              <span class="text-[10px] text-neutral-400 font-mono">COBRApy &amp; RBA</span>
            </div>
          </template>
        </UCard>

        <!-- Bento Card 4: GINsim Qualitative Networks -->
        <UCard class="shadow-sm border border-neutral-200 dark:border-neutral-800 flex flex-col justify-between">
          <template #header>
            <div class="flex items-center justify-between gap-2">
              <div class="flex items-center gap-2.5">
                <div class="size-9 rounded-lg bg-purple-500/10 text-purple-600 dark:text-purple-400 flex items-center justify-center">
                  <UIcon name="i-lucide-network" class="size-5" />
                </div>
                <div>
                  <h3 class="text-sm font-semibold text-neutral-900 dark:text-white">
                    GINsim Logical Networks
                  </h3>
                  <p class="text-[11px] text-neutral-500">
                    Qualitative gene &amp; signaling networks
                  </p>
                </div>
              </div>
              <UBadge color="secondary" variant="subtle" size="md" class="shrink-0">
                SBML-qual
              </UBadge>
            </div>
          </template>

          <div class="space-y-3 text-xs text-neutral-600 dark:text-neutral-400 leading-relaxed">
            <p>
              <strong class="text-neutral-900 dark:text-white">GINsim</strong> (Gene Interaction Network simulation) specifies discrete logical and multi-valued regulatory networks.
            </p>
            <p>
              Converts <code class="font-mono text-neutral-800 dark:text-neutral-200">.ginml</code> files into interactive node-link Vega network diagrams. Node activation states, inhibitory interactions, and discrete state transitions are reflected dynamically as simulations execute.
            </p>

            <div class="p-2.5 rounded-lg bg-neutral-50 dark:bg-neutral-800/60 border border-neutral-100 dark:border-neutral-800 space-y-1">
              <span class="block text-[10px] uppercase font-semibold text-neutral-400">Supported Formats:</span>
              <div class="flex items-center gap-1.5 flex-wrap font-mono text-[11px]">
                <span class="px-1.5 py-0.5 rounded bg-neutral-200/70 dark:bg-neutral-700/60 text-neutral-800 dark:text-neutral-200">.ginml / .zginml</span>
                <UIcon name="i-lucide-arrow-right" class="size-3 text-neutral-400" />
                <span class="px-1.5 py-0.5 rounded bg-primary/10 text-primary font-semibold">.vg.json (Vega)</span>
              </div>
            </div>
          </div>

          <template #footer>
            <div class="flex items-center justify-between text-xs pt-1">
              <UButton
                to="http://ginsim.org/"
                target="_blank"
                variant="link"
                color="primary"
                size="xs"
                icon="i-lucide-external-link"
                label="GINsim Project"
                class="p-0 text-[11px]"
              />
              <span class="text-[10px] text-neutral-400 font-mono">Logical modeling</span>
            </div>
          </template>
        </UCard>

        <!-- Bento Card 5: SBGN Process & Activity Flow -->
        <UCard class="shadow-sm border border-neutral-200 dark:border-neutral-800 flex flex-col justify-between">
          <template #header>
            <div class="flex items-center justify-between gap-2">
              <div class="flex items-center gap-2.5">
                <div class="size-9 rounded-lg bg-sky-500/10 text-sky-600 dark:text-sky-400 flex items-center justify-center">
                  <UIcon name="i-lucide-workflow" class="size-5" />
                </div>
                <div>
                  <h3 class="text-sm font-semibold text-neutral-900 dark:text-white">
                    SBGN Process Diagrams
                  </h3>
                  <p class="text-[11px] text-neutral-500">
                    Standard biological process notation
                  </p>
                </div>
              </div>
              <UBadge color="primary" variant="subtle" size="md" class="shrink-0">
                SBGN-ML
              </UBadge>
            </div>
          </template>

          <div class="space-y-3 text-xs text-neutral-600 dark:text-neutral-400 leading-relaxed">
            <p>
              The <strong class="text-neutral-900 dark:text-white">Systems Biology Graphical Notation (SBGN)</strong> provides standardized graphical languages for biochemistry.
            </p>
            <p>
              Converts SBGN-ML Process Description (PD) and Activity Flow (AF) maps into vector Vega glyphs. Kinetic concentration trajectories and state variables are dynamically bound to species glyphs and transition nodes.
            </p>

            <div class="p-2.5 rounded-lg bg-neutral-50 dark:bg-neutral-800/60 border border-neutral-100 dark:border-neutral-800 space-y-1">
              <span class="block text-[10px] uppercase font-semibold text-neutral-400">Supported Formats:</span>
              <div class="flex items-center gap-1.5 flex-wrap font-mono text-[11px]">
                <span class="px-1.5 py-0.5 rounded bg-neutral-200/70 dark:bg-neutral-700/60 text-neutral-800 dark:text-neutral-200">.sbgn / .xml</span>
                <UIcon name="i-lucide-arrow-right" class="size-3 text-neutral-400" />
                <span class="px-1.5 py-0.5 rounded bg-primary/10 text-primary font-semibold">.vg.json (Vega)</span>
              </div>
            </div>
          </div>

          <template #footer>
            <div class="flex items-center justify-between text-xs pt-1">
              <UButton
                to="https://sbgn.github.io/"
                target="_blank"
                variant="link"
                color="primary"
                size="xs"
                icon="i-lucide-external-link"
                label="SBGN Standard"
                class="p-0 text-[11px]"
              />
              <span class="text-[10px] text-neutral-400 font-mono">PD &amp; AF maps</span>
            </div>
          </template>
        </UCard>
      </div>
    </div>

      <!-- Bento Bottom Card: Best Practices for COMBINE Packaging -->
      <UCard class="shadow-sm border border-neutral-200 dark:border-neutral-800">
        <div class="flex flex-col md:flex-row items-start md:items-center justify-between gap-6">
          <div class="space-y-1.5 max-w-3xl">
            <div class="flex items-center gap-2">
              <span class="size-2 rounded-full bg-primary" />
              <h3 class="text-sm font-semibold text-neutral-900 dark:text-white">
                Best Practices for COMBINE/OMEX Visualization Packaging
              </h3>
            </div>
            <p class="text-xs text-neutral-500 dark:text-neutral-400 leading-relaxed">
              When authoring custom Vega visualizations, ensure that the <code class="font-mono text-primary text-[11px]">"data"</code> blocks in your Vega specification declare names that match the SED-ML report tasks (e.g. <code class="font-mono text-[11px]">report_1</code>). Register the file in your archive manifest as <code class="font-mono text-[11px]">http://identifiers.org/combine.specifications/vega</code> to enable automatic rendering in the BioSimulations experiment dashboard.
            </p>
          </div>

          <div class="flex items-center gap-3 shrink-0">
            <UButton
              to="/utilities/new-simulation-experiment"
              color="primary"
              size="sm"
              icon="i-lucide-folder-plus"
              label="Build COMBINE Archive"
            />
          </div>
        </div>
      </UCard>
    </div>
  </div>
</template>
