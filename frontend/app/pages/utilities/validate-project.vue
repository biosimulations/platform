<script setup lang="ts">
import { computed, ref } from 'vue';
import { useClipboard } from '@vueuse/core';
import type { BreadcrumbItem } from '#ui/components/Breadcrumb.vue';
import type { TabsItem } from '#ui/components/Tabs.vue';
import type { RadioGroupItem } from '#ui/components/RadioGroup.vue';
import {
  COMBINE_PROJECT_FORMAT_OPTION,
  OMEX_METADATA_FORMAT_OPTIONS,
  type OmexMetadataFormatOption,
  type OmexMetadataSchema,
  type ValidateProjectOptions
} from '~/models/combine-api';
import { useCombineApi } from '~/composables/useCombineApi';
import { useValidation } from '~/composables/useValidation';
import ValidationForm from '~/components/utilities/ValidationForm.vue';
import ValidationReportView from '~/components/utilities/ValidationReportView.vue';

useSeoMeta({
  title: 'Validate COMBINE Project — BioSimulations',
  ogTitle: 'Validate COMBINE Project — BioSimulations',
  description: 'Validate full COMBINE/OMEX archives (.omex, .zip) with comprehensive verification of manifests, SED-ML simulation experiments, linked computational models, and OMEX metadata annotations.',
  ogDescription: 'Validate full COMBINE/OMEX archives (.omex, .zip) with comprehensive verification of manifests, SED-ML simulation experiments, linked computational models, and OMEX metadata annotations.'
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
    label: 'Validate COMBINE Project'
  }
];

// Validation Options State
const validateOmexManifest = ref<boolean>(true);
const validateSedml = ref<boolean>(true);
const validateSedmlModels = ref<boolean>(true);
const validateOmexMetadata = ref<boolean>(true);
const validateImages = ref<boolean>(true);

const selectedFormat = ref<OmexMetadataFormatOption>(OMEX_METADATA_FORMAT_OPTIONS[0]!);
const selectedSchema = ref<OmexMetadataSchema>('BioSimulations');

const schemaRadioItems: RadioGroupItem[] = [
  {
    label: 'BioSimulations Schema (Recommended)',
    description: 'Enforces BioSimulations conventions and minimal required properties (title, creators, description, license) needed for publication.',
    value: 'BioSimulations'
  },
  {
    label: 'RDF Triples (General Semantic)',
    description: 'Validates general RDF graph syntax and ontology triples without domain-specific BioSimulations publishing restrictions.',
    value: 'rdf_triples'
  }
];

const route = useRoute();
const initialUrl = computed(() => {
  const q = route.query.projectUrl || route.query.url;
  return typeof q === 'string' ? q : '';
});

const { validateProject } = useCombineApi();
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
  const flags: string[] = [];
  if (!validateOmexManifest.value) flags.push('--no-validate-manifest');
  if (!validateSedml.value) flags.push('--no-validate-sedml');
  if (!validateSedmlModels.value) flags.push('--no-validate-models');
  if (!validateOmexMetadata.value) flags.push('--no-validate-metadata');
  if (!validateImages.value) flags.push('--no-validate-images');

  const flagStr = flags.length > 0 ? ` ${flags.join(' ')}` : '';

  return {
    cli: {
      cmd: `biosimulators-utils validate-project path/to/archive.omex${flagStr}`,
      description: 'Validate your COMBINE/OMEX archive locally from the terminal using biosimulators-utils.'
    },
    python: {
      cmd: `from biosimulators_utils.combine.validation import validate\n\nerrors, warnings = validate(\n    'path/to/archive.omex',\n    validate_manifest=${validateOmexManifest.value ? 'True' : 'False'},\n    validate_sedml=${validateSedml.value ? 'True' : 'False'},\n    validate_models=${validateSedmlModels.value ? 'True' : 'False'},\n    validate_metadata=${validateOmexMetadata.value ? 'True' : 'False'},\n    validate_images=${validateImages.value ? 'True' : 'False'}\n)\n\nif not errors:\n    print('COMBINE archive is valid!')\nelse:\n    print(f'Validation failed: {errors}')`,
      description: 'Programmatically validate archives and inspect manifest entries, SED-ML tasks, and model references in Python scripts or CI pipelines.'
    },
    docker: {
      cmd: `docker run --rm -v $(pwd):/workspace \\\n  ghcr.io/biosimulators/biosimulators-utils:latest \\\n  biosimulators-utils validate-project /workspace/archive.omex${flagStr}`,
      description: 'Execute the full validation suite in an isolated, containerized environment with all system dependencies pre-configured.'
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
  const options: ValidateProjectOptions = {
    omexMetadataFormat: selectedFormat.value.id,
    omexMetadataSchema: selectedSchema.value,
    validateOmexManifest: validateOmexManifest.value,
    validateSedml: validateSedml.value,
    validateSedmlModels: validateSedmlModels.value,
    validateOmexMetadata: validateOmexMetadata.value,
    validateImages: validateImages.value
  };

  await runValidation(() => validateProject(target, options));
}

function handleReset() {
  reset();
}
</script>

<template>
  <div class="min-h-screen bg-neutral-50 py-8 px-4 sm:px-6 lg:px-8">
    <div class="max-w-7xl mx-auto space-y-8">
      <!-- Breadcrumbs & Header -->
      <div>
        <UBreadcrumb :items="breadcrumbs" class="mb-3" />

        <div class="flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
          <div>
            <h1 class="text-3xl font-bold tracking-tight text-neutral-900 flex items-center gap-3">
              <UIcon name="i-lucide-folder-check" class="size-8 text-primary" />
              Validate COMBINE Project
            </h1>
            <p class="mt-1 text-sm text-neutral-600 max-w-3xl">
              Verify full COMBINE/OMEX simulation archives (.omex, .zip) with granular checking of manifests, SED-ML experiment workflows, computational models, metadata annotations, and digital assets.
            </p>
          </div>

          <div class="flex items-center gap-2">
            <UButton
              to="https://docs.biosimulations.org/users/validating-projects/"
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
            :accept="COMBINE_PROJECT_FORMAT_OPTION.accept"
            :supported-extensions="COMBINE_PROJECT_FORMAT_OPTION.extensions"
            :initial-url="initialUrl"
            submit-label="Validate Project"
            drop-label="Drop COMBINE/OMEX archive here (.omex, .zip)"
            @submit="handleValidationSubmit"
            @reset="handleReset"
          >
            <!-- Slot for Project Configuration Options -->
            <template #options>
              <div class="space-y-6">
                <!-- Granular Verification Scope Toggles -->
                <div class="space-y-3">
                  <div class="flex items-center justify-between">
                    <label class="block text-xs font-semibold text-neutral-600 uppercase tracking-wider">
                      Verification Scope & Checks
                    </label>
                    <a
                      href="https://combinearchive.org/"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="text-xs text-primary hover:underline flex items-center gap-1"
                    >
                      Archive Specs
                      <UIcon name="i-lucide-external-link" class="size-3" />
                    </a>
                  </div>

                  <div class="p-4 rounded-lg bg-neutral-100 border border-neutral-200 space-y-4">
                    <!-- Toggle 1: Manifest -->
                    <div class="flex items-start justify-between gap-3">
                      <div class="space-y-0.5">
                        <div class="flex items-center gap-2 text-xs font-semibold text-neutral-900">
                          <UIcon name="i-lucide-file-text" class="size-4 text-primary shrink-0" />
                          <span>Validate OMEX Manifest (<code class="text-[11px] font-mono">manifest.xml</code>)</span>
                        </div>
                        <p class="text-[11px] text-neutral-500 pl-6 leading-relaxed">
                          Verifies that all entries within the archive are cataloged with recognized COMBINE MIME types or format identifiers.
                        </p>
                      </div>
                      <USwitch
                        v-model="validateOmexManifest"
                        color="primary"
                        checked-icon="i-lucide-check"
                        unchecked-icon="i-lucide-x"
                      />
                    </div>

                    <!-- Toggle 2: SED-ML -->
                    <div class="flex items-start justify-between gap-3 pt-3 border-t border-neutral-200">
                      <div class="space-y-0.5">
                        <div class="flex items-center gap-2 text-xs font-semibold text-neutral-900">
                          <UIcon name="i-lucide-activity" class="size-4 text-primary shrink-0" />
                          <span>Validate SED-ML Experiments</span>
                        </div>
                        <p class="text-[11px] text-neutral-500 pl-6 leading-relaxed">
                          Validates syntax, tasks, data generators, MathML expressions, and KiSAO algorithms across all SED-ML documents.
                        </p>
                      </div>
                      <USwitch
                        v-model="validateSedml"
                        color="primary"
                        checked-icon="i-lucide-check"
                        unchecked-icon="i-lucide-x"
                      />
                    </div>

                    <!-- Toggle 3: Models -->
                    <div class="flex items-start justify-between gap-3 pt-3 border-t border-neutral-200">
                      <div class="space-y-0.5">
                        <div class="flex items-center gap-2 text-xs font-semibold text-neutral-900">
                          <UIcon name="i-lucide-file-code" class="size-4 text-primary shrink-0" />
                          <span>Validate Linked Simulation Models</span>
                        </div>
                        <p class="text-[11px] text-neutral-500 pl-6 leading-relaxed">
                          Checks the internal syntax and validity of computational models (SBML, CellML, BNGL, etc.) referenced by SED-ML tasks.
                        </p>
                      </div>
                      <USwitch
                        v-model="validateSedmlModels"
                        color="primary"
                        checked-icon="i-lucide-check"
                        unchecked-icon="i-lucide-x"
                      />
                    </div>

                    <!-- Toggle 4: Metadata -->
                    <div class="flex items-start justify-between gap-3 pt-3 border-t border-neutral-200">
                      <div class="space-y-0.5">
                        <div class="flex items-center gap-2 text-xs font-semibold text-neutral-900">
                          <UIcon name="i-lucide-tags" class="size-4 text-primary shrink-0" />
                          <span>Validate OMEX Metadata Documents</span>
                        </div>
                        <p class="text-[11px] text-neutral-500 pl-6 leading-relaxed">
                          Checks semantic annotations, authorship attribution, open licenses, and biological descriptors across metadata documents.
                        </p>
                      </div>
                      <USwitch
                        v-model="validateOmexMetadata"
                        color="primary"
                        checked-icon="i-lucide-check"
                        unchecked-icon="i-lucide-x"
                      />
                    </div>

                    <!-- Toggle 5: Images -->
                    <div class="flex items-start justify-between gap-3 pt-3 border-t border-neutral-200">
                      <div class="space-y-0.5">
                        <div class="flex items-center gap-2 text-xs font-semibold text-neutral-900">
                          <UIcon name="i-lucide-image" class="size-4 text-primary shrink-0" />
                          <span>Validate Image & Thumbnail Assets</span>
                        </div>
                        <p class="text-[11px] text-neutral-500 pl-6 leading-relaxed">
                          Verifies the formatting and integrity of image files (PNG, JPEG, WEBP, etc.) bundled in the project.
                        </p>
                      </div>
                      <USwitch
                        v-model="validateImages"
                        color="primary"
                        checked-icon="i-lucide-check"
                        unchecked-icon="i-lucide-x"
                      />
                    </div>
                  </div>
                </div>

                <!-- Metadata Settings (Shown when validateOmexMetadata is active) -->
                <div v-if="validateOmexMetadata" class="space-y-5 p-4 rounded-lg bg-neutral-100 border border-neutral-200">
                  <div class="flex items-center justify-between">
                    <label class="block text-xs font-semibold text-neutral-700 uppercase tracking-wider">
                      Metadata Format & Schema
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

                  <!-- Format Dropdown -->
                  <div class="space-y-2">
                    <label class="block text-xs font-medium text-neutral-700">
                      Expected Metadata Serialization
                    </label>
                    <USelectMenu
                      v-model="selectedFormat"
                      :items="OMEX_METADATA_FORMAT_OPTIONS"
                      by="id"
                      label-key="name"
                      class="w-full"
                    />
                    <p class="text-[11px] text-neutral-500">
                      {{ selectedFormat.description }}
                    </p>
                  </div>

                  <!-- Schema Radio Group -->
                  <div class="space-y-2 pt-2 border-t border-neutral-200">
                    <label class="block text-xs font-medium text-neutral-700">
                      Metadata Schema Rule
                    </label>
                    <URadioGroup
                      v-model="selectedSchema"
                      :items="schemaRadioItems"
                      class="space-y-2.5"
                    />
                  </div>
                </div>

                <!-- Contextual Guidance Notice -->
                <div class="p-3.5 rounded-lg bg-amber-500/10 border border-amber-500/20 text-xs text-amber-900 space-y-2">
                  <div class="flex items-start gap-2 font-medium">
                    <UIcon name="i-lucide-info" class="size-4 shrink-0 mt-0.5 text-amber-600" />
                    <span>Project Archive Scope & Requirements:</span>
                  </div>
                  <p class="leading-relaxed pl-6 text-neutral-700">
                    An OMEX Manifest (<code class="text-[11px] font-mono">manifest.xml</code>) is required to parse and validate internal OMEX Metadata documents. If manifest checking is disabled, metadata validation should also be toggled off.
                  </p>
                  <p class="leading-relaxed pl-6 text-neutral-600">
                    File uploads are limited to 64 MB (archives up to 1 GB can be submitted via URL). Sample COMBINE archives are available in the
                    <a
                      href="https://github.com/biosimulations/Biosimulators_test_suite/tree/deploy/examples"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="underline font-semibold hover:text-amber-800"
                    >
                      BioSimulators test suite examples
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
            artifact-name="COMBINE/OMEX Project Archive"
          />
        </div>

        <!-- Right: Information & Ecosystem (5 cols on lg) -->
        <div class="lg:col-span-5 space-y-6">
          <!-- Card 1: Local & CI/CD Validation -->
          <UCard class="shadow-sm border border-neutral-200">
            <template #header>
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <UIcon name="i-lucide-terminal" class="size-5 text-primary" />
                  <h3 class="text-sm font-semibold text-neutral-900">
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

              <p class="text-xs text-neutral-600">
                {{ cliSnippets[activeCliTab]?.description }}
              </p>

              <div class="relative">
                <pre class="bg-neutral-900 text-neutral-100 p-3 rounded-lg text-xs font-mono overflow-x-auto whitespace-pre leading-relaxed border border-neutral-800"><code>{{ cliSnippets[activeCliTab]?.cmd }}</code></pre>
              </div>
            </div>
          </UCard>

          <!-- Card 2: Standards & Compliance -->
          <UCard class="shadow-sm border border-neutral-200">
            <template #header>
              <div class="flex items-center gap-2">
                <UIcon name="i-lucide-shield-check" class="size-5 text-primary" />
                <h3 class="text-sm font-semibold text-neutral-900">
                  COMBINE Project Verification Criteria
                </h3>
              </div>
            </template>

            <div class="space-y-3 text-xs text-neutral-600 leading-relaxed">
              <p>
                To guarantee reproducibility across biological modeling tools, COMBINE/OMEX archives undergo five-stage verification:
              </p>

              <div class="space-y-2.5 pt-1">
                <div class="flex items-start gap-2.5">
                  <UIcon name="i-lucide-check" class="size-4 text-success shrink-0 mt-0.5" />
                  <div>
                    <strong class="text-neutral-900">Manifest Declaration:</strong>
                    Verifies that every archive entry is declared in <code class="text-[11px] font-mono">manifest.xml</code> with valid format URIs.
                  </div>
                </div>

                <div class="flex items-start gap-2.5">
                  <UIcon name="i-lucide-check" class="size-4 text-success shrink-0 mt-0.5" />
                  <div>
                    <strong class="text-neutral-900">SED-ML Experiments:</strong>
                    Ensures simulation tasks, uniform time courses, data generators, and output definitions are fully compliant with SED-ML Level 1.
                  </div>
                </div>

                <div class="flex items-start gap-2.5">
                  <UIcon name="i-lucide-check" class="size-4 text-success shrink-0 mt-0.5" />
                  <div>
                    <strong class="text-neutral-900">Linked Model Consistency:</strong>
                    Verifies syntax of model documents (SBML, CellML, BNGL, etc.) and ensures that SED-ML model source targets exist.
                  </div>
                </div>

                <div class="flex items-start gap-2.5">
                  <UIcon name="i-lucide-check" class="size-4 text-success shrink-0 mt-0.5" />
                  <div>
                    <strong class="text-neutral-900">BioSimulations Metadata:</strong>
                    Validates that OMEX metadata graphs include required provenance: titles, abstracts, creator ORCID identifiers, and open licenses.
                  </div>
                </div>

                <div class="flex items-start gap-2.5">
                  <UIcon name="i-lucide-check" class="size-4 text-success shrink-0 mt-0.5" />
                  <div>
                    <strong class="text-neutral-900">Media & Image Integrity:</strong>
                    Verifies that diagram previews, charts, and thumbnails can be read and decoded correctly.
                  </div>
                </div>
              </div>
            </div>
          </UCard>

          <!-- Card 3: Publishing Requirements -->
          <UCard class="shadow-sm border border-neutral-200">
            <template #header>
              <div class="flex items-center gap-2">
                <UIcon name="i-lucide-book-open" class="size-5 text-primary" />
                <h3 class="text-sm font-semibold text-neutral-900">
                  Publishing to BioSimulations
                </h3>
              </div>
            </template>

            <div class="space-y-3 text-xs text-neutral-600">
              <p>
                All simulation projects submitted to the BioSimulations repository must pass archive validation checks to ensure long-term reproducibility and discoverability.
              </p>

              <div class="pt-2 border-t border-neutral-200 space-y-2">
                <div class="font-medium text-neutral-800">
                  Helpful Resources:
                </div>
                <ul class="space-y-1.5 list-disc list-inside">
                  <li>
                    <a
                      href="https://combinearchive.org/"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="text-primary hover:underline"
                    >
                      COMBINE Archive Specification
                    </a>
                  </li>
                  <li>
                    <a
                      href="https://docs.biosimulations.org/concepts/conventions/simulation-project-metadata/"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="text-primary hover:underline"
                    >
                      Simulation Project Metadata Conventions
                    </a>
                  </li>
                  <li>
                    <a
                      href="https://sed-ml.org/"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="text-primary hover:underline"
                    >
                      SED-ML Specifications
                    </a>
                  </li>
                </ul>
              </div>

              <div class="pt-3 border-t border-neutral-200 flex items-center justify-between">
                <span class="text-[11px] text-neutral-500">Ready to simulate your project?</span>
                <UButton
                  to="/simulations/run"
                  size="xs"
                  variant="link"
                  color="primary"
                  icon="i-lucide-arrow-right"
                  trailing
                >
                  Run Simulation
                </UButton>
              </div>
            </div>
          </UCard>
        </div>
      </div>
    </div>
  </div>
</template>
