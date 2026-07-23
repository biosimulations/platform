<script setup lang="ts">
import {DotLottieVue} from "@lottiefiles/dotlottie-vue";

defineProps<{
  message?: string
  error?: string
}>()

const emit = defineEmits<{
  (e: 'refresh', value: Event): void
}>()

function refresh(e: Event) {
  emit('refresh', e)
}
</script>

<template>
  <div class="w-full md:w-max md:max-w-175 lg:max-w-225 flex flex-col items-center justify-center gap-2">
    <DotLottieVue
      class="w-37.5 aspect-square"
      autoplay
      src="/animations/error.lottie"
    />
    <h1 class="text-2xl font-bold">
      {{ message ?? 'An unknown error occurred. Please refresh or try again later.' }}
    </h1>
    <pre v-if="error" class="bg-neutral-100 rounded p-2">{{ error }}</pre>

    <div class="w-full flex-1 flex items-center justify-center gap-3 mt-4">
      <UButton
        color="primary"
        class="cursor-pointer"
        to="/"
        icon="i-lucide-home"
        label="Go Home"
      />
      <UButton
        color="neutral"
        class="cursor-pointer"
        variant="outline"
        icon="i-lucide-rotate-ccw"
        label="Retry"
        @click="refresh($event)"
      />
    </div>
  </div>
</template>
