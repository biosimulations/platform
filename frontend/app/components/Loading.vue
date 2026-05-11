<script setup lang="ts">
  const props = defineProps({
    message: String
  })
</script>

<template>
  <div class="w-max flex items-center gap-2">
    <Icon name="svg-spinners:gooey-balls-2" class="size-8 text-primary"></Icon>
    <span class="font-semibold">{{ message ?? 'Loading...'}}</span>
  </div>
</template>
