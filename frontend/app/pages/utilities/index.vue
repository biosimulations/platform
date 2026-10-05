<script setup lang="ts">
import { computed, ref } from 'vue';
import type { BreadcrumbItem } from '#ui/components/Breadcrumb.vue';

useSeoMeta({
  title: 'Utilities - BioSimulations',
  description: 'Explore, validate, simulate, and inspect standard-compliant biological models, simulation experiments, and COMBINE archives.'
});

const breadcrumbs: BreadcrumbItem[] = [
  {
    label: 'Home',
    icon: 'i-lucide-home',
    to: '/'
  },
  {
    label: 'Utilities'
  }
];

interface UtilityItem {
  id: string;
  title: string;
  description: string;
  icon: string;
  to: string;
  external?: boolean;
  keywords: string[];
}

const utilities: UtilityItem[] = [
  {
    id: 'validate-model',
    title: 'Validate Model',
    description: 'Verify syntax, semantics, and compliance of computational models against COMBINE standards across 9 supported languages (SBML, CellML, BNGL, Smoldyn, etc.).',
    icon: 'i-lucide-file-check',
    to: '/utilities/validate-model',
    keywords: ['model', 'sbml', 'cellml', 'bngl', 'smoldyn', 'ginsim', 'neuroml', 'lems', 'rba', 'xpp', 'validate', 'check', 'syntax', 'semantics']
  },
  {
    id: 'validate-simulation',
    title: 'Validate Simulation (SED-ML)',
    description: 'Verify syntax, MathML formulas, and KiSAO algorithms of Simulation Experiment Description Markup Language (SED-ML) files across Level 1 (V1–V4).',
    icon: 'i-lucide-activity',
    to: '/utilities/validate-simulation',
    keywords: ['simulation', 'sedml', 'sed-ml', 'kisao', 'mathml', 'experiment', 'uniform time course', 'validate', 'check']
  },
  {
    id: 'verify-model',
    title: 'Verify Model (BioCheckNet)',
    description: 'Multi-solver simulation verification engine to cross-compare trajectories, quantify numerical concordance, and flag outliers.',
    icon: 'i-lucide-shield-check',
    to: '/utilities/verify-model',
    keywords: ['verify', 'verification', 'concordance', 'multi-solver', 'trajectories', 'reproducibility', 'cross-check', 'residuals', 'biochecknet', 'outliers', 'omex', 'zip']
  },
  {
    id: 'describe-visualizations',
    title: 'Describe Visualizations',
    description: 'Explore supported biological visualization frameworks including Vega data specifications, Escher metabolic maps, and SBGN pathways.',
    icon: 'i-lucide-pie-chart',
    to: '/utilities/describe-visualizations',
    keywords: ['visualizations', 'describe', 'vega', 'escher', 'sbgn', 'ginsim', 'plots', 'charts', 'maps', 'pathways']
  },
  {
    id: 'suggest-simulator',
    title: 'Suggest Simulator',
    description: 'Traverse the KiSAO ontology graph to find compatible simulation algorithms and discover matching BioSimulators execution engines.',
    icon: 'i-lucide-lightbulb',
    to: '/simulators/suggest',
    keywords: ['suggest', 'simulator', 'kisao', 'algorithm', 'ontology', 'engine', 'compatibility', 'recommend', 'match']
  },
  {
    id: 'validate-simulator',
    title: 'Validate Simulator',
    description: 'Verify that a containerized simulation engine complies with BioSimulators Docker specifications and standard execution interfaces.',
    icon: 'i-lucide-cpu',
    to: '/simulators/validate',
    keywords: ['simulator', 'docker', 'container', 'validate', 'biosimulators', 'test', 'engine']
  },
  {
    id: 'validate-metadata',
    title: 'Validate Metadata',
    description: 'Validate OMEX metadata annotations in RDF/XML, Turtle, NTriples, and JSON-LD formats against BioSimulations and RDF triple schemas.',
    icon: 'i-lucide-tags',
    to: '/utilities/validate-metadata',
    keywords: ['metadata', 'omex', 'rdf', 'turtle', 'triples', 'jsonld', 'annotations', 'validate', 'schema']
  },
  {
    id: 'validate-project',
    title: 'Validate COMBINE Project',
    description: 'Validate full COMBINE/OMEX archives with granular verification of manifests, SED-ML files, models, and metadata.',
    icon: 'i-lucide-folder-check',
    to: '/utilities/validate-project',
    keywords: ['project', 'combine', 'omex', 'archive', 'zip', 'manifest', 'models', 'sedml', 'validate']
  },
  {
    id: 'new-simulation-experiment',
    title: 'New Simulation Experiment',
    description: 'Step-by-step wizard to configure SED-ML simulation experiments, modify initial conditions, and assemble runnable COMBINE archives.',
    icon: 'i-lucide-folder-plus',
    to: '/utilities/new-simulation-experiment',
    keywords: ['new', 'experiment', 'create', 'wizard', 'sedml', 'omex', 'builder', 'parameters', 'assembly']
  },
  {
    id: 'combine-api',
    title: 'COMBINE Web Services API',
    description: 'REST API microservices for manipulating, converting, validating, and introspecting COMBINE archives, models, SED-ML, and metadata.',
    icon: 'i-lucide-server',
    to: 'https://combine.api.biosimulations.org',
    external: true,
    keywords: ['api', 'combine', 'rest', 'openapi', 'services', 'endpoints', 'introspection', 'developer', 'swagger']
  },
  {
    id: 'verification-api',
    title: 'BioCheckNet Verification API',
    description: 'High-performance REST API for executing automated multi-solver verification workflows, trajectory comparisons, and concordance metrics.',
    icon: 'i-lucide-terminal',
    to: 'https://biochecknet.biosimulations.org/docs',
    external: true,
    keywords: ['biochecknet', 'verification', 'api', 'rest', 'automated', 'concordance', 'solvers', 'developer', 'workflows']
  }
];

const searchQuery = ref('');

const filteredUtilities = computed(() => {
  const q = searchQuery.value.toLowerCase().trim();
  if (!q) return utilities;

  const titleMatches: UtilityItem[] = [];
  const descriptionMatches: UtilityItem[] = [];
  const keywordMatches: UtilityItem[] = [];

  for (const u of utilities) {
    if (u.title.toLowerCase().includes(q)) {
      titleMatches.push(u);
    } else if (u.description.toLowerCase().includes(q)) {
      descriptionMatches.push(u);
    } else if (u.keywords.some(k => k.toLowerCase().includes(q))) {
      keywordMatches.push(u);
    }
  }

  return [...titleMatches, ...descriptionMatches, ...keywordMatches];
});
</script>

<template>
  <div class="min-h-screen bg-neutral-50 dark:bg-neutral-950 py-8 px-4 sm:px-6 lg:px-8">
    <div class="max-w-7xl mx-auto space-y-8">
      <!-- Breadcrumbs & Hero Header -->
      <div class="space-y-4">
        <UBreadcrumb :items="breadcrumbs" class="mb-2">
          <template #separator>
            <span class="mx-1 text-neutral-400 dark:text-neutral-600">/</span>
          </template>
        </UBreadcrumb>

        <div class="flex flex-col md:flex-row md:items-end justify-between gap-4">
          <div>
            <h1 class="text-3xl sm:text-4xl font-extrabold tracking-tight text-neutral-900 dark:text-neutral-100">
              Utilities
            </h1>
            <p class="mt-2 text-sm sm:text-base text-neutral-600 dark:text-neutral-400 max-w-3xl leading-relaxed">
              Discover and execute tools to validate models, author simulation experiments, verify numerical reproducibility across solvers, and inspect COMBINE archives.
            </p>
          </div>

          <div class="flex items-center gap-2 shrink-0">
            <UButton
              to="https://docs.biosimulations.org"
              target="_blank"
              rel="noopener noreferrer"
              color="neutral"
              variant="outline"
              icon="i-lucide-external-link"
            >
              Documentation
            </UButton>
          </div>
        </div>
      </div>

      <!-- Search Bar -->
      <UInput
        v-model="searchQuery"
        leading-icon="i-lucide-search"
        placeholder="Search utilities by keyword, format, or name..."
        size="lg"
        class="w-full"
      >
        <template #trailing>
          <UButton
            v-if="searchQuery"
            icon="i-lucide-x"
            color="neutral"
            variant="link"
            size="xs"
            aria-label="Clear search"
            @click="searchQuery = ''"
          />
        </template>
      </UInput>

      <!-- Utilities Grid -->
      <div v-if="filteredUtilities.length > 0" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        <UPageCard
          v-for="item in filteredUtilities"
          :key="item.id"
          :title="item.title"
          :description="item.description"
          :icon="item.icon"
          :to="item.to"
          :target="item.external ? '_blank' : undefined"
          :rel="item.external ? 'noopener noreferrer' : undefined"
          variant="subtle"
          spotlight
          class="flex flex-col justify-between group hover:-translate-y-0.5 transition-all duration-200"
          :ui="{
            container: 'flex flex-col flex-1 p-5 gap-3',
            wrapper: 'flex flex-col flex-1',
            title: 'text-base font-semibold group-hover:text-primary transition-colors',
            description: 'text-xs text-neutral-600 dark:text-neutral-400 leading-relaxed mt-1'
          }"
        />
      </div>

      <!-- Empty State -->
      <UEmpty
        v-else
        icon="i-lucide-search-x"
        title="No utilities match your search"
        description="We couldn't find any utilities matching your search keyword. Try searching for common modeling standards like SBML, SED-ML, or OMEX."
        variant="subtle"
        class="py-12"
      >
        <template #actions>
          <UButton
            color="primary"
            variant="solid"
            icon="i-lucide-rotate-ccw"
            label="Reset Search"
            @click="searchQuery = ''"
          />
        </template>
      </UEmpty>
    </div>
  </div>
</template>
