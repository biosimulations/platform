<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { TabsItem } from '#ui/components/Tabs.vue';

interface Props {
  isLoading?: boolean;
  accept?: string;
  supportedExtensions?: string[];
  submitLabel?: string;
  dropLabel?: string;
  initialUrl?: string;
}

const props = withDefaults(defineProps<Props>(), {
  isLoading: false,
  accept: '*',
  supportedExtensions: () => [],
  submitLabel: 'Validate',
  dropLabel: 'Drop file here',
  initialUrl: ''
});

const emit = defineEmits<{
  (e: 'submit', payload: { type: 'file'; file: File } | { type: 'url'; url: string }): void;
  (e: 'reset'): void;
}>();

const mode = ref<'file' | 'url'>(props.initialUrl ? 'url' : 'file');
const selectedFile = ref<File | null>(null);
const inputUrl = ref(props.initialUrl || '');
const urlError = ref<string | null>(null);

const inputModes: TabsItem[] = [
  { label: 'Upload File', value: 'file', icon: 'i-lucide-upload-cloud' },
  { label: 'Provide URL', value: 'url', icon: 'i-lucide-link' }
];

const canSubmit = computed(() => {
  if (props.isLoading) return false;
  if (mode.value === 'file') {
    return selectedFile.value !== null;
  }
  return inputUrl.value.trim().length > 0;
});

function validateUrl(val: string): boolean {
  if (!val.trim()) {
    urlError.value = 'Please provide a valid URL.';
    return false;
  }
  try {
    const url = new URL(val.trim());
    if (url.protocol !== 'http:' && url.protocol !== 'https:') {
      urlError.value = 'URL must start with http:// or https://';
      return false;
    }
    urlError.value = null;
    return true;
  } catch {
    urlError.value = 'Please enter a valid URL.';
    return false;
  }
}

watch(inputUrl, (val) => {
  if (urlError.value && val) {
    validateUrl(val);
  }
});

watch(() => props.initialUrl, (newVal) => {
  if (newVal) {
    inputUrl.value = newVal;
    mode.value = 'url';
  }
});

function handleSubmit() {
  if (mode.value === 'file') {
    if (!selectedFile.value) return;
    emit('submit', { type: 'file', file: selectedFile.value });
  } else {
    if (!validateUrl(inputUrl.value)) return;
    emit('submit', { type: 'url', url: inputUrl.value.trim() });
  }
}

function handleReset() {
  selectedFile.value = null;
  inputUrl.value = '';
  urlError.value = null;
  emit('reset');
}
</script>

<template>
  <UCard class="w-full shadow-sm border border-neutral-200 dark:border-neutral-800">
    <template #header>
      <div class="flex items-center justify-between gap-4">
        <div>
          <h2 class="text-base font-semibold text-neutral-900 dark:text-neutral-100 flex items-center gap-2">
            <UIcon name="i-lucide-file-input" class="size-5 text-primary" />
            Input Source & Settings
          </h2>
          <p class="text-xs text-neutral-500 dark:text-neutral-400 mt-0.5">
            Select a local file or provide a public URL to validate.
          </p>
        </div>
      </div>
    </template>

    <div class="space-y-6">
      <!-- Options Slot (Parent language selector, switches, etc.) -->
      <slot name="options" />

      <!-- Input Mode Switcher -->
      <div class="space-y-3">
        <label class="block text-xs font-semibold text-neutral-600 dark:text-neutral-300 uppercase tracking-wider">
          Input Method
        </label>
        <UTabs
          v-model="mode"
          :items="inputModes"
          class="w-full"
        />
      </div>

      <!-- File Mode using canonical UFileUpload -->
      <div v-if="mode === 'file'" class="pt-1">
        <UFileUpload
          v-model="selectedFile"
          :accept="accept"
          layout="list"
          icon="i-lucide-upload-cloud"
          :label="dropLabel"
          class="w-full min-h-40"
          :disabled="isLoading"
        >
          <template #description>
            <div class="flex flex-col items-center gap-1 mt-1">
              <span class="text-xs text-neutral-500 dark:text-neutral-400">or click to browse from your device</span>
              <div v-if="supportedExtensions.length > 0" class="flex flex-wrap items-center justify-center gap-1.5 mt-1.5">
                <span class="text-xs text-neutral-400">Accepted formats:</span>
                <UBadge
                  v-for="ext in supportedExtensions"
                  :key="ext"
                  size="md"
                  variant="subtle"
                  color="neutral"
                >
                  {{ ext }}
                </UBadge>
              </div>
            </div>
          </template>
        </UFileUpload>
      </div>

      <!-- URL Mode -->
      <div v-else class="space-y-3 pt-1">
        <div class="space-y-1.5">
          <label class="block text-sm font-medium text-neutral-700 dark:text-neutral-200">
            Public File URL
          </label>
          <div class="flex gap-2">
            <UInput
              v-model="inputUrl"
              placeholder="https://example.org/path/to/model.xml"
              class="flex-1"
              icon="i-lucide-globe"
              :disabled="isLoading"
              @keydown.enter.prevent="handleSubmit"
            />
            <UButton
              v-if="inputUrl"
              variant="ghost"
              color="neutral"
              icon="i-lucide-x"
              :disabled="isLoading"
              @click="inputUrl = ''"
            />
          </div>
          <p v-if="urlError" class="text-xs text-error font-medium flex items-center gap-1 mt-1">
            <UIcon name="i-lucide-alert-circle" class="size-3.5" />
            {{ urlError }}
          </p>
          <p v-else class="text-xs text-neutral-500 dark:text-neutral-400">
            Ensure the URL points directly to the raw model file (CORS or direct public download).
          </p>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="flex items-center justify-between gap-4">
        <UButton
          variant="ghost"
          color="neutral"
          icon="i-lucide-rotate-ccw"
          :disabled="isLoading || (!selectedFile && !inputUrl)"
          @click="handleReset"
        >
          Reset
        </UButton>

        <UButton
          color="primary"
          icon="i-lucide-play"
          :loading="isLoading"
          :disabled="!canSubmit"
          @click="handleSubmit"
        >
          {{ submitLabel }}
        </UButton>
      </div>
    </template>
  </UCard>
</template>
