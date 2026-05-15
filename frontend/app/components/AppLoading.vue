<script setup lang="ts">
defineProps<{
  message?: string
}>()
</script>

<template>
  <div class="w-max flex items-center gap-2">
    <Icon
      name="svg-spinners:gooey-balls-2"
      class="size-8 text-primary"
    />
    <span class="font-semibold">{{ message ?? 'Loading...' }}</span>
  </div>
</template>
