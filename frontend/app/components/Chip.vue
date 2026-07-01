<script setup lang="ts">
defineProps({
  label: String,
  removable: Boolean
})

const emit = defineEmits<{
  (e: 'removed', value: Event): void
}>()

function handle_remove($event: Event) {
  emit('removed', $event)
}
</script>

<template>
  <div class="py-1 px-2 rounded-md bg-primary-500/10 border border-primary-500/50 text-primary flex items-center justify-center">
    <p class="text-sm font-medium">{{ label }}</p>
    <UTooltip v-if="removable" :text="`Remove ${label}`">
      <UIcon role="button" aria-label="Remove search filter: {{ label }}" class="size-4 ml-2 cursor-pointer text-primary hover:text-primary-800" name="i-lucide-x" @click="handle_remove($event)"></UIcon>
    </UTooltip>
  </div>
</template>
