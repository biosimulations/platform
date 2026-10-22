<script setup lang="ts">
import { computed, ref } from 'vue';
import { useClipboard } from '@vueuse/core';
import type { BreadcrumbItem } from '#ui/components/Breadcrumb.vue';
import type { TabsItem } from '#ui/components/Tabs.vue';
import {
  MODEL_LANGUAGE_OPTIONS,
  type ModelLanguageOption
} from '~/models/combine-api';
import { useCombineApi } from '~/composables/useCombineApi';
import { useValidation } from '~/composables/useValidation';
import ValidationForm from '~/components/utilities/ValidationForm.vue';
import ValidationReportView from '~/components/utilities/ValidationReportView.vue';

useSeoMeta({
  title: 'Validate Model - BioSimulations',
  description: 'Validate SBML, CellML, BNGL, Smoldyn, GINsim, NeuroML, LEMS, RBA, and XPP models against COMBINE community standards.'
});

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
    label: 'Validate Model'
  }
];

const selectedLanguage = ref<ModelLanguageOption>(MODEL_LANGUAGE_OPTIONS[0]!);

const { validateModel } = useCombineApi();
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
  const lang = selectedLanguage.value.id;
  const ext = selectedLanguage.value.extensions[0] || '.xml';
  return {
    cli: {
      cmd: `biosimulators-utils validate-model path/to/model${ext} ${lang}`,
      description: `Validate your ${selectedLanguage.value.name} file locally from the command line using biosimulators-utils.`
    },
    python: {
      cmd: `from biosimulators_utils.model_lang.${lang.toLowerCase()}.validation import validate_model\n\nerrors, warnings, _ = validate_model('path/to/model${ext}')\nif not errors:\n    print('Model is valid!')\nelse:\n    print(f'Validation failed: {errors}')`,
      description: `Programmatically check model validity and obtain detailed diagnostics within Python scripts or test pipelines.`
    },
    docker: {
      cmd: `docker run --rm -v $(pwd):/workspace \\\n  ghcr.io/biosimulators/biosimulators-utils:latest \\\n  biosimulators-utils validate-model /workspace/model${ext} ${lang}`,
      description: `Execute the validation suite in an isolated, containerized environment with all system dependencies pre-configured.`
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
  await runValidation(() => validateModel(target, selectedLanguage.value.id));
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
        <UBreadcrumb :items="breadcrumbs" class="mb-3" />

        <div class="flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
          <div>
            <h1 class="text-3xl font-bold tracking-tight text-neutral-900 dark:text-neutral-100 flex items-center gap-3">
              <UIcon name="i-lucide-check-square" class="size-8 text-primary" />
              Validate Model
            </h1>
            <p class="mt-1 text-sm text-neutral-600 dark:text-neutral-400 max-w-3xl">
              Verify the syntax, semantics, and compliance of your computational models against COMBINE community standards across 9 supported languages (SBML, CellML, BNGL, Smoldyn, GINsim, NeuroML, LEMS, RBA, and XPP).
            </p>
          </div>

          <div class="flex items-center gap-2">
            <UButton
              to="https://docs.biosimulations.org/users/validating-models/"
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

      <!-- Main Layout Grid -->
      <div class="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
        <!-- Left: Form & Results (7 cols on lg) -->
        <div class="lg:col-span-7 space-y-6">
          <ValidationForm
            :is-loading="isLoading"
            :accept="selectedLanguage.accept"
            :supported-extensions="selectedLanguage.extensions"
            submit-label="Validate Model"
            @submit="handleValidationSubmit"
            @reset="handleReset"
          >
            <!-- Slot for Language Configuration -->
            <template #options>
              <div class="space-y-3">
                <div class="flex items-center justify-between">
                  <label class="block text-xs font-semibold text-neutral-600 dark:text-neutral-300 uppercase tracking-wider">
                    Model Language
                  </label>
                  <a
                    v-if="selectedLanguage.docsUrl"
                    :href="selectedLanguage.docsUrl"
                    target="_blank"
                    rel="noopener noreferrer"
                    class="text-xs text-primary hover:underline flex items-center gap-1"
                  >
                    Format Specs
                    <UIcon name="i-lucide-external-link" class="size-3" />
                  </a>
                </div>

                <USelectMenu
                  v-model="selectedLanguage"
                  :items="MODEL_LANGUAGE_OPTIONS"
                  label-key="name"
                  class="w-full"
                />

                <!-- Language Info Box -->
                <div class="p-3 rounded-lg bg-neutral-100 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 space-y-2">
                  <p class="text-xs text-neutral-600 dark:text-neutral-400">
                    {{ selectedLanguage.description }}
                  </p>
                  <div class="flex flex-wrap items-center gap-1.5 pt-1">
                    <span class="text-xs text-neutral-500">Extensions:</span>
                    <UBadge
                      v-for="ext in selectedLanguage.extensions"
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
            </template>
          </ValidationForm>

          <!-- Report View -->
          <ValidationReportView
            :report="report"
            :is-loading="isLoading"
            artifact-name="Model"
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

          <!-- Card 2: Validation Standards & Compliance -->
          <UCard class="shadow-sm border border-neutral-200 dark:border-neutral-800">
            <template #header>
              <div class="flex items-center gap-2">
                <UIcon name="i-lucide-shield-check" class="size-5 text-primary" />
                <h3 class="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
                  Validation Standards
                </h3>
              </div>
            </template>

            <div class="space-y-3 text-xs text-neutral-600 dark:text-neutral-400 leading-relaxed">
              <div class="flex items-start gap-2.5">
                <UIcon name="i-lucide-check" class="size-4 text-success shrink-0 mt-0.5" />
                <div>
                  <strong class="text-neutral-900 dark:text-neutral-200">Syntax & Schema Conformance:</strong>
                  Validates strict adherence to official XML schemas (e.g. SBML Level 3 Version 2, CellML 2.0).
                </div>
              </div>
              <div class="flex items-start gap-2.5">
                <UIcon name="i-lucide-check" class="size-4 text-success shrink-0 mt-0.5" />
                <div>
                  <strong class="text-neutral-900 dark:text-neutral-200">Unit Consistency Checks:</strong>
                  Identifies undefined units in mathematical rate rules, kinetic laws, parameters, and compartment sizes.
                </div>
              </div>
              <div class="flex items-start gap-2.5">
                <UIcon name="i-lucide-check" class="size-4 text-success shrink-0 mt-0.5" />
                <div>
                  <strong class="text-neutral-900 dark:text-neutral-200">Cross-Platform Reproducibility:</strong>
                  Ensures models can be executed without translation errors across all compatible BioSimulators engines.
                </div>
              </div>

              <div class="pt-2 border-t border-neutral-100 dark:border-neutral-800 flex items-center justify-between">
                <span class="text-[11px] text-neutral-500">Powered by COMBINE & BioSimulators</span>
                <UButton
                  to="https://co.mbine.org/standards/"
                  target="_blank"
                  rel="noopener noreferrer"
                  size="xs"
                  variant="link"
                  color="primary"
                  icon="i-lucide-arrow-right"
                  trailing
                >
                  COMBINE Standards
                </UButton>
              </div>
            </div>
          </UCard>
        </div>
      </div>
    </div>
  </div>
</template>
