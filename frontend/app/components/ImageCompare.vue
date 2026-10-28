<script setup lang="ts">
const position = ref(50)

defineProps({
  before_img: String,
  after_img: String,
})
</script>

<template>
  <div class="relative w-full max-w-(--ui-container) aspect-video overflow-hidden">
    <img :src="after_img" alt="After" class="absolute inset-0 size-full object-cover">
    <img
      :src="before_img"
      alt="Before"
      class="absolute inset-0 size-full object-cover"
      :style="{ clipPath: `inset(0 ${100 - position}% 0 0)` }"
    >
    <USlider
      v-model="position"
      class="absolute inset-x-0 bottom-4 px-4"
    />
  </div>
</template>
