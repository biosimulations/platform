<script setup lang="ts">
import { computed, ref } from 'vue';
import { useClipboard } from '@vueuse/core';
import type { BreadcrumbItem } from '#ui/components/Breadcrumb.vue';
import type { TabsItem } from '#ui/components/Tabs.vue';
import type { RadioGroupItem } from '#ui/components/RadioGroup.vue';
import {
  OMEX_METADATA_FORMAT_OPTIONS,
  type OmexMetadataFormatOption,
  type OmexMetadataSchema
} from '~/models/combine-api';
import { useCombineApi } from '~/composables/useCombineApi';
import { useValidation } from '~/composables/useValidation';
import ValidationForm from '~/components/utilities/ValidationForm.vue';
import ValidationReportView from '~/components/utilities/ValidationReportView.vue';

useSeoMeta({
  title: 'Validate Metadata — BioSimulations',
  description: 'Verify the syntax, semantics, and compliance of OMEX metadata annotations in RDF/XML, Turtle, N-Triples, N-Quads, and RDFa formats against BioSimulations and RDF triple schemas.'
});

const breadcrumbs: BreadcrumbItem[] = [
  {
    label: 'Home',
    icon: 'i-lucide-home',
    to: '/'
  },
  {
    label: 'Utilities',
    to: '/utilities'
  },
  {
    label: 'Validate Metadata'
  }
];

const selectedFormat = ref<OmexMetadataFormatOption>(OMEX_METADATA_FORMAT_OPTIONS[0]!);
const selectedSchema = ref<OmexMetadataSchema>('BioSimulations');

const schemaRadioItems: RadioGroupItem[] = [
  {
    label: 'BioSimulations Schema (Recommended)',
    description: 'Enforces BioSimulations conventions and minimal metadata requirements (title, creators, description, license) needed to publish projects.',
    value: 'BioSimulations'
  },
  {
    label: 'RDF Triples (General Semantic)',
    description: 'Validates standard RDF graph syntax and ontology triples without domain-specific BioSimulations publishing restrictions.',
    value: 'rdf_triples'
  }
];

const route = useRoute();
const initialUrl = computed(() => {
  const q = route.query.metadataUrl || route.query.url;
  return typeof q === 'string' ? q : '';
});

const { validateOmexMetadata } = useCombineApi();
const { isLoading, report, runValidation, reset } = useValidation();
const { copy } = useClipboard();
const toast = useToast();

const activeCliTab = ref('cli');

const cliTabs: TabsItem[] = [
  { label: 'CLI', value: 'cli', icon: 'i-lucide-terminal' },
  { label: 'Python SDK', value: 'python', icon: 'i-lucide-code-2' },
  { label: 'Docker', value: 'docker', icon: 'i-lucide-container' }
];

const cliSnippets = computed<Record<string, { cmd: string; description: string }>>(() => {
  const format = selectedFormat.value.id;
  const ext = selectedFormat.value.extensions[0] || '.rdf';
  const schema = selectedSchema.value;
  return {
    cli: {
      cmd: `biosimulators-utils validate-metadata path/to/metadata${ext} --format ${format} --schema ${schema}`,
      description: `Validate your ${selectedFormat.value.name} metadata annotations locally using the biosimulators-utils CLI.`
    },
    python: {
      cmd: `from biosimulators_utils.omex_meta.validation import validate_biosimulations_metadata\n\nerrors, warnings = validate_biosimulations_metadata('path/to/metadata${ext}')\nif not errors:\n    print('Metadata is valid!')\nelse:\n    print(f'Validation failed: {errors}')`,
      description: `Programmatically validate OMEX metadata graphs and verify required annotations in Python scripts or CI pipelines.`
    },
    docker: {
      cmd: `docker run --rm -v $(pwd):/workspace \\\n  ghcr.io/biosimulators/biosimulators-utils:latest \\\n  biosimulators-utils validate-metadata /workspace/metadata${ext} --format ${format} --schema ${schema}`,
      description: `Run the validation suite in an isolated, containerized environment with libOmexMeta and RDF parsers pre-installed.`
    }
  };
});

function copySnippet(text: string) {
  copy(text);
  toast.add({
    title: 'Command copied',
    description: 'Code snippet copied to clipboard.',
    color: 'success',
    icon: 'i-lucide-check'
  });
}

async function handleValidationSubmit(payload: { type: 'file'; file: File } | { type: 'url'; url: string }) {
  const target = payload.type === 'file' ? payload.file : payload.url;
  await runValidation(() => validateOmexMetadata(target, selectedFormat.value.id, selectedSchema.value));
}

function handleReset() {
  reset();
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
            <h1 class="text-3xl font-bold tracking-tight text-neutral-900 dark:text-neutral-100 flex items-center gap-3">
              <UIcon name="i-lucide-tags" class="size-8 text-primary" />
              Validate Metadata
            </h1>
            <p class="mt-1 text-sm text-neutral-600 dark:text-neutral-400 max-w-3xl">
              Verify the syntax, structure, and semantic annotations of OMEX metadata files across 5 serializations against BioSimulations publishing guidelines and W3C RDF standards.
            </p>
          </div>

          <div class="flex items-center gap-2">
            <UButton
              to="https://docs.biosimulations.org/concepts/conventions/simulation-project-metadata/"
              target="_blank"
              rel="noopener noreferrer"
              color="neutral"
              variant="outline"
              icon="i-lucide-external-link"
            >
              Metadata Conventions
            </UButton>
          </div>
        </div>
      </div>

      <!-- Main Layout Grid -->
      <div class="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
        <!-- Left: Form & Results (7 cols on lg) -->
        <div class="lg:col-span-7 space-y-6">
          <ValidationForm
            :is-loading="isLoading"
            :accept="selectedFormat.accept"
            :supported-extensions="selectedFormat.extensions"
            :initial-url="initialUrl"
            submit-label="Validate Metadata"
            @submit="handleValidationSubmit"
            @reset="handleReset"
          >
            <!-- Slot for Metadata Configuration (Format & Schema) -->
            <template #options>
              <div class="space-y-6">
                <!-- Format Selector -->
                <div class="space-y-3">
                  <div class="flex items-center justify-between">
                    <label class="block text-xs font-semibold text-neutral-600 dark:text-neutral-300 uppercase tracking-wider">
                      Metadata Format
                    </label>
                    <a
                      v-if="selectedFormat.docsUrl"
                      :href="selectedFormat.docsUrl"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="text-xs text-primary hover:underline flex items-center gap-1"
                    >
                      Format Specs
                      <UIcon name="i-lucide-external-link" class="size-3" />
                    </a>
                  </div>

                  <USelectMenu
                    v-model="selectedFormat"
                    :items="OMEX_METADATA_FORMAT_OPTIONS"
                    by="id"
                    label-key="name"
                    class="w-full"
                  />

                  <!-- Format Info Box -->
                  <div class="p-3 rounded-lg bg-neutral-100 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 space-y-2">
                    <p class="text-xs text-neutral-600 dark:text-neutral-400">
                      {{ selectedFormat.description }}
                    </p>
                    <div class="flex flex-wrap items-center gap-1.5 pt-1">
                      <span class="text-xs text-neutral-500">Extensions:</span>
                      <UBadge
                        v-for="ext in selectedFormat.extensions"
                        :key="ext"
                        size="md"
                        variant="subtle"
                        color="primary"
                      >
                        {{ ext }}
                      </UBadge>
                    </div>
                  </div>
                </div>

                <!-- Schema Selector -->
                <div class="space-y-3">
                  <div class="flex items-center justify-between">
                    <label class="block text-xs font-semibold text-neutral-600 dark:text-neutral-300 uppercase tracking-wider">
                      Validation Schema
                    </label>
                    <a
                      href="https://docs.biosimulations.org/concepts/conventions/simulation-project-metadata/"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="text-xs text-primary hover:underline flex items-center gap-1"
                    >
                      Schema Guide
                      <UIcon name="i-lucide-external-link" class="size-3" />
                    </a>
                  </div>

                  <URadioGroup
                    v-model="selectedSchema"
                    :items="schemaRadioItems"
                    value-key="value"
                    variant="card"
                    class="space-y-2"
                  />
                </div>

                <!-- Contextual Guidance & Limitations Notice -->
                <div class="p-3.5 rounded-lg bg-amber-500/10 border border-amber-500/20 text-xs text-amber-900 dark:text-amber-200 space-y-2">
                  <div class="flex items-start gap-2 font-medium">
                    <UIcon name="i-lucide-info" class="size-4 shrink-0 mt-0.5 text-amber-600 dark:text-amber-400" />
                    <span>Single-File Validation & Project Scope:</span>
                  </div>
                  <p class="leading-relaxed pl-6 text-neutral-700 dark:text-neutral-300">
                    Although metadata can be described across multiple OMEX Metadata files, this utility validates an individual metadata file. If your project splits metadata across files or references thumbnail image files, validate the entire archive with the
                    <NuxtLink to="/utilities/validate-project" class="underline font-semibold hover:text-primary">
                      Validate COMBINE Project
                    </NuxtLink>
                    utility.
                  </p>
                  <p class="leading-relaxed pl-6 text-neutral-600 dark:text-neutral-400">
                    File uploads are limited to 64 MB (files up to 1 GB can be submitted via URL). Sample metadata files are available in the
                    <a
                      href="https://github.com/biosimulations/Biosimulators_test_suite/tree/deploy/examples"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="text-primary hover:underline inline-flex items-center gap-0.5 font-medium"
                    >
                      BioSimulators test suite examples
                      <UIcon name="i-lucide-external-link" class="size-3" />
                    </a>.
                  </p>
                </div>
              </div>
            </template>
          </ValidationForm>

          <!-- Report View -->
          <ValidationReportView
            :report="report"
            :is-loading="isLoading"
            artifact-name="Metadata Document"
          />
        </div>

        <!-- Right: Information & Ecosystem (5 cols on lg) -->
        <div class="lg:col-span-5 space-y-6">
          <!-- Card 1: Local & CI/CD Validation -->
          <UCard class="shadow-sm border border-neutral-200 dark:border-neutral-800">
            <template #header>
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <UIcon name="i-lucide-terminal" class="size-5 text-primary" />
                  <h3 class="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
                    Local & CI/CD Validation
                  </h3>
                </div>
                <UButton
                  size="xs"
                  variant="ghost"
                  color="neutral"
                  icon="i-lucide-copy"
                  @click="copySnippet(cliSnippets[activeCliTab]?.cmd || '')"
                >
                  Copy
                </UButton>
              </div>
            </template>

            <div class="space-y-3">
              <UTabs
                v-model="activeCliTab"
                :items="cliTabs"
                :content="false"
                class="w-full"
              />

              <p class="text-xs text-neutral-600 dark:text-neutral-400">
                {{ cliSnippets[activeCliTab]?.description }}
              </p>

              <div class="relative">
                <pre class="bg-neutral-900 text-neutral-100 p-3 rounded-lg text-xs font-mono overflow-x-auto whitespace-pre leading-relaxed border border-neutral-800"><code>{{ cliSnippets[activeCliTab]?.cmd }}</code></pre>
              </div>
            </div>
          </UCard>

          <!-- Card 2: BioSimulations Minimal Metadata Schema -->
          <UCard class="shadow-sm border border-neutral-200 dark:border-neutral-800">
            <template #header>
              <div class="flex items-center gap-2">
                <UIcon name="i-lucide-shield-check" class="size-5 text-primary" />
                <h3 class="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
                  Required Properties for Publishing
                </h3>
              </div>
            </template>

            <div class="space-y-3 text-xs text-neutral-600 dark:text-neutral-400 leading-relaxed">
              <p>
                To publish simulation projects to the BioSimulations repository, OMEX metadata documents must satisfy minimal metadata requirements:
              </p>

              <div class="space-y-2.5 pt-1">
                <div class="flex items-start gap-2.5">
                  <UIcon name="i-lucide-check" class="size-4 text-success shrink-0 mt-0.5" />
                  <div>
                    <strong class="text-neutral-900 dark:text-neutral-200">Title & Abstract:</strong>
                    Concise project title (<code class="text-[11px] font-mono">dc:title</code>) and detailed summary (<code class="text-[11px] font-mono">dc:description</code>).
                  </div>
                </div>

                <div class="flex items-start gap-2.5">
                  <UIcon name="i-lucide-check" class="size-4 text-success shrink-0 mt-0.5" />
                  <div>
                    <strong class="text-neutral-900 dark:text-neutral-200">Creators & Attribution:</strong>
                    Names and ORCID persistent identifiers for authors and model curators (<code class="text-[11px] font-mono">dc:creator</code>).
                  </div>
                </div>

                <div class="flex items-start gap-2.5">
                  <UIcon name="i-lucide-check" class="size-4 text-success shrink-0 mt-0.5" />
                  <div>
                    <strong class="text-neutral-900 dark:text-neutral-200">Open License:</strong>
                    SPDX identifier or URI granting terms of reuse (e.g. CC0, MIT, CC-BY) (<code class="text-[11px] font-mono">schema:license</code>).
                  </div>
                </div>

                <div class="flex items-start gap-2.5">
                  <UIcon name="i-lucide-check" class="size-4 text-success shrink-0 mt-0.5" />
                  <div>
                    <strong class="text-neutral-900 dark:text-neutral-200">Publication Citations:</strong>
                    Articles describing the model via PubMed, DOI, or EuropePMC identifiers (<code class="text-[11px] font-mono">bqmodel:isDescribedBy</code>).
                  </div>
                </div>
              </div>
            </div>
          </UCard>

          <!-- Card 3: Ecosystem & Guidelines -->
          <UCard class="shadow-sm border border-neutral-200 dark:border-neutral-800">
            <template #header>
              <div class="flex items-center gap-2">
                <UIcon name="i-lucide-book-open" class="size-5 text-primary" />
                <h3 class="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
                  COMBINE Metadata Ecosystem
                </h3>
              </div>
            </template>

            <div class="space-y-3 text-xs text-neutral-600 dark:text-neutral-400">
              <p>
                OMEX Metadata follows the recommendations of the COMBINE community and is parsed using the libOmexMeta library.
              </p>

              <div class="pt-2 border-t border-neutral-200 dark:border-neutral-800 space-y-2">
                <div class="font-medium text-neutral-800 dark:text-neutral-200">
                  Helpful Resources:
                </div>
                <ul class="space-y-1.5 list-disc list-inside">
                  <li>
                    <a
                      href="https://sys-bio.github.io/libOmexMeta/"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="text-primary hover:underline"
                    >
                      libOmexMeta Documentation
                    </a>
                  </li>
                  <li>
                    <a
                      href="https://co.mbine.org/standards/omex-metadata"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="text-primary hover:underline"
                    >
                      COMBINE OMEX Metadata Standard
                    </a>
                  </li>
                  <li>
                    <a
                      href="https://docs.biosimulations.org/concepts/conventions/simulation-project-metadata/"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="text-primary hover:underline"
                    >
                      BioSimulations Conventions Guide
                    </a>
                  </li>
                </ul>
              </div>
            </div>
          </UCard>
        </div>
      </div>
    </div>
  </div>
</template>
