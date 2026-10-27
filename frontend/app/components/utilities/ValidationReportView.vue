<script setup lang="ts">
import { computed } from 'vue';
import { useClipboard } from '@vueuse/core';
import type { ValidationReport } from '~/models/combine-api';

interface Props {
  report?: ValidationReport | null;
  isLoading?: boolean;
  artifactName?: string;
}

const props = withDefaults(defineProps<Props>(), {
  report: null,
  isLoading: false,
  artifactName: 'Model'
});

const { copy } = useClipboard();
const toast = useToast();

const errorCount = computed(() => props.report?.errors?.length || 0);
const warningCount = computed(() => props.report?.warnings?.length || 0);

const statusConfig = computed(() => {
  if (!props.report) return null;
  switch (props.report.status) {
    case 'valid':
      return {
        color: 'success' as const,
        icon: 'i-lucide-check-circle-2',
        title: `${props.artifactName} is Valid`,
        description: `The ${props.artifactName.toLowerCase()} fully conforms to community specifications with zero errors or warnings.`
      };
    case 'warnings':
      return {
        color: 'warning' as const,
        icon: 'i-lucide-alert-triangle',
        title: `${props.artifactName} Valid with Warnings`,
        description: `The ${props.artifactName.toLowerCase()} is syntactically valid but produced ${warningCount.value} non-fatal warning${warningCount.value === 1 ? '' : 's'}.`
      };
    case 'invalid':
    default:
      return {
        color: 'error' as const,
        icon: 'i-lucide-x-circle',
        title: `${props.artifactName} Validation Failed`,
        description: `Found ${errorCount.value} issue${errorCount.value === 1 ? '' : 's'} that violate the specification.`
      };
  }
});

function copyReportJson() {
  if (!props.report) return;
  copy(JSON.stringify(props.report, null, 2));
  toast.add({
    title: 'Report Copied',
    description: 'Full validation diagnostics copied to clipboard as JSON.',
    color: 'success',
    icon: 'i-lucide-copy'
  });
}

function downloadReportJson() {
  if (!props.report) return;
  const blob = new Blob([JSON.stringify(props.report, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${props.artifactName.toLowerCase()}-validation-report.json`;
  a.click();
  URL.revokeObjectURL(url);
  toast.add({
    title: 'Report Downloaded',
    description: 'Saved diagnostics file to your downloads.',
    color: 'success',
    icon: 'i-lucide-download'
  });
}
</script>

<template>
  <div class="w-full space-y-6">
    <!-- Loading State -->
    <UCard
      v-if="isLoading"
      class="w-full border border-neutral-200 dark:border-neutral-800 shadow-sm"
    >
      <div class="py-12 flex flex-col items-center justify-center text-center space-y-4">
        <UIcon name="i-svg-spinners-90-ring-with-bg" class="size-10 text-primary animate-spin" />
        <div>
          <h3 class="text-base font-semibold text-neutral-900 dark:text-neutral-100">
            Validating {{ artifactName }}...
          </h3>
          <p class="text-sm text-neutral-500 dark:text-neutral-400 mt-1">
            Running syntax checks and semantic verification via the COMBINE validation service.
          </p>
        </div>
      </div>
    </UCard>

    <!-- Report Available -->
    <div v-else-if="report && statusConfig" class="space-y-6">
      <!-- Status Banner -->
      <UAlert
        :color="statusConfig.color"
        variant="subtle"
        :icon="statusConfig.icon"
        :title="statusConfig.title"
        :description="statusConfig.description"
        class="border"
      >
        <template #actions>
          <div class="flex items-center gap-2">
            <UButton
              size="xs"
              variant="outline"
              :color="statusConfig.color"
              icon="i-lucide-copy"
              @click="copyReportJson"
            >
              Copy JSON
            </UButton>
            <UButton
              size="xs"
              variant="subtle"
              :color="statusConfig.color"
              icon="i-lucide-download"
              @click="downloadReportJson"
            >
              Export
            </UButton>
          </div>
        </template>
      </UAlert>

      <!-- Metric Badges Bar -->
      <div class="flex flex-wrap items-center gap-3 p-4 rounded-xl border border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900 shadow-sm">
        <span class="text-xs font-semibold text-neutral-500 uppercase tracking-wider">
          Results Summary:
        </span>
        <UBadge
          size="md"
          :color="statusConfig.color"
          variant="solid"
          class="uppercase tracking-wide font-bold"
        >
          Status: {{ report.status }}
        </UBadge>

        <UBadge
          size="md"
          :color="errorCount > 0 ? 'error' : 'neutral'"
          :variant="errorCount > 0 ? 'subtle' : 'outline'"
        >
          {{ errorCount }} Error{{ errorCount === 1 ? '' : 's' }}
        </UBadge>

        <UBadge
          size="md"
          :color="warningCount > 0 ? 'warning' : 'neutral'"
          :variant="warningCount > 0 ? 'subtle' : 'outline'"
        >
          {{ warningCount }} Warning{{ warningCount === 1 ? '' : 's' }}
        </UBadge>
      </div>

      <!-- Errors Section -->
      <UCard
        v-if="report.errors && report.errors.length > 0"
        class="border border-error/30 dark:border-error/20 shadow-sm"
      >
        <template #header>
          <div class="flex items-center justify-between">
            <h3 class="text-sm font-semibold text-error flex items-center gap-2">
              <UIcon name="i-lucide-x-circle" class="size-4" />
              Errors ({{ report.errors.length }})
            </h3>
            <span class="text-xs text-neutral-400">Must be resolved for execution</span>
          </div>
        </template>

        <div class="space-y-4">
          <div
            v-for="(err, idx) in report.errors"
            :key="idx"
            class="p-4 rounded-lg bg-error-50/50 dark:bg-error-950/20 border border-error-100 dark:border-error-900/40 space-y-2"
          >
            <div class="flex items-start gap-2.5">
              <span class="size-5 rounded-full bg-error-100 dark:bg-error-900/60 text-error flex items-center justify-center text-xs font-bold shrink-0 mt-0.5">
                {{ idx + 1 }}
              </span>
              <div class="min-w-0 flex-1">
                <p class="text-sm font-medium text-neutral-900 dark:text-neutral-100 whitespace-pre-wrap">
                  {{ err.summary }}
                </p>

                <!-- Nested Details Collapsible -->
                <UCollapsible
                  v-if="err.details && err.details.length > 0"
                  class="mt-2.5"
                >
                  <UButton
                    size="xs"
                    variant="ghost"
                    color="neutral"
                    :label="`${err.details.length} Diagnostic Detail${err.details.length === 1 ? '' : 's'}`"
                    trailing-icon="i-lucide-chevron-down"
                    class="group -ml-2"
                    :ui="{ trailingIcon: 'group-data-[state=open]:rotate-180 transition-transform duration-200' }"
                  />
                  <template #content>
                    <div class="mt-2 pl-3 border-l-2 border-error-300 dark:border-error-800 space-y-2">
                      <div
                        v-for="(detail, dIdx) in err.details"
                        :key="dIdx"
                        class="p-2.5 rounded-md bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 text-xs font-mono text-neutral-700 dark:text-neutral-300 whitespace-pre-wrap"
                      >
                        {{ detail.summary }}

                        <!-- Level 2 nested details if any -->
                        <div v-if="detail.details && detail.details.length > 0" class="mt-2 pl-2 border-l border-neutral-300 dark:border-neutral-700 space-y-1">
                          <p v-for="(sub, sIdx) in detail.details" :key="sIdx" class="text-xs text-neutral-500">
                            {{ sub.summary }}
                          </p>
                        </div>
                      </div>
                    </div>
                  </template>
                </UCollapsible>
              </div>
            </div>
          </div>
        </div>
      </UCard>

      <!-- Warnings Section -->
      <UCard
        v-if="report.warnings && report.warnings.length > 0"
        class="border border-warning/30 dark:border-warning/20 shadow-sm"
      >
        <template #header>
          <div class="flex items-center justify-between">
            <h3 class="text-sm font-semibold text-warning-600 dark:text-warning-400 flex items-center gap-2">
              <UIcon name="i-lucide-alert-triangle" class="size-4" />
              Warnings ({{ report.warnings.length }})
            </h3>
            <span class="text-xs text-neutral-400">Non-fatal compliance recommendations</span>
          </div>
        </template>

        <div class="space-y-4">
          <div
            v-for="(warn, idx) in report.warnings"
            :key="idx"
            class="p-4 rounded-lg bg-warning-50/50 dark:bg-warning-950/20 border border-warning-100 dark:border-warning-900/40 space-y-2"
          >
            <div class="flex items-start gap-2.5">
              <span class="size-5 rounded-full bg-warning-100 dark:bg-warning-900/60 text-warning-700 dark:text-warning-300 flex items-center justify-center text-xs font-bold shrink-0 mt-0.5">
                {{ idx + 1 }}
              </span>
              <div class="min-w-0 flex-1">
                <p class="text-sm font-medium text-neutral-900 dark:text-neutral-100 whitespace-pre-wrap">
                  {{ warn.summary }}
                </p>

                <!-- Nested Details Collapsible -->
                <UCollapsible
                  v-if="warn.details && warn.details.length > 0"
                  class="mt-2.5"
                >
                  <UButton
                    size="xs"
                    variant="ghost"
                    color="neutral"
                    :label="`${warn.details.length} Diagnostic Detail${warn.details.length === 1 ? '' : 's'}`"
                    trailing-icon="i-lucide-chevron-down"
                    class="group -ml-2"
                    :ui="{ trailingIcon: 'group-data-[state=open]:rotate-180 transition-transform duration-200' }"
                  />
                  <template #content>
                    <div class="mt-2 pl-3 border-l-2 border-warning-300 dark:border-warning-800 space-y-2">
                      <div
                        v-for="(detail, dIdx) in warn.details"
                        :key="dIdx"
                        class="p-2.5 rounded-md bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 text-xs font-mono text-neutral-700 dark:text-neutral-300 whitespace-pre-wrap"
                      >
                        {{ detail.summary }}

                        <!-- Level 2 nested details if any -->
                        <div v-if="detail.details && detail.details.length > 0" class="mt-2 pl-2 border-l border-neutral-300 dark:border-neutral-700 space-y-1">
                          <p v-for="(sub, sIdx) in detail.details" :key="sIdx" class="text-xs text-neutral-500">
                            {{ sub.summary }}
                          </p>
                        </div>
                      </div>
                    </div>
                  </template>
                </UCollapsible>
              </div>
            </div>
          </div>
        </div>
      </UCard>
    </div>
  </div>
</template>
