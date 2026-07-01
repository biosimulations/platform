<script setup lang="ts">
import type {AppChip} from "~/models/common";

defineProps<{
  chips: AppChip[]
}>()

const emit = defineEmits<{
  (e: 'removed', value: string): void
}>()

function handle_remove(chip_slug: string) {
  emit('removed', chip_slug)
}
</script>

<template>
  <div class="w-full flex flex-wrap items-center justify-start gap-2">
    <Chip v-for="chip in chips" :key="chip.slug" :label="chip.label" :removable="chip.removable" @removed="handle_remove(chip.slug)"></Chip>
  </div>
</template>
