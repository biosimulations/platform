<script setup lang="ts">
import { normalize_text } from '~/functions/functions'
import type { BreadcrumbItem } from '#ui/components/Breadcrumb.vue'

const route = useRoute()
const routes = route.path.split('/').filter(i => i && i.trim().length > 0)
const breadcrumbs = ref<BreadcrumbItem[]>([])

onMounted(() => {
  routes.forEach((route) => {
    const breadcrumb = {
      label: normalize_text(route),
      to: `/${route}`
    }

    breadcrumbs.value.push(breadcrumb)
  })
})
</script>

<template>
  <section class="w-full min-h-[calc(100vh-var(--ui-header-height))] px-6 max-w-[1200px] mx-auto flex flex-col gap-4 items-center justify-center text-center md:text-left">
    <UBreadcrumb :items="breadcrumbs" />

    <!-- <div v-if="processing_status == null" class="flex items-center gap-2">
      <Loading />
    </div> -->
  </section>
</template>
