<script setup lang="ts">
import type {AppChip} from "~/models/common";

defineProps<{
  chips: AppChip[]
  loading: Boolean
}>()

const emit = defineEmits<{
  (e: 'removed', value: AppChip): void
}>()

function handle_remove(chip: AppChip) {
  emit('removed', chip)
}
</script>

<template>
  <div class="w-full flex flex-wrap items-center justify-start gap-2">
    <Chip v-for="chip in chips" :key="chip.slug" :loading="loading" :label="chip.label" :removable="chip.removable" @removed="handle_remove(chip)"></Chip>
  </div>
</template>
