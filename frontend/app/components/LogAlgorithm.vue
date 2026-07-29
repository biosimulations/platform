<template>
  <div v-if="algorithmTerm" class="mt-2 text-sm">
    <h4 class="font-bold">
      Executed algorithm: {{ algorithmTerm.name }} (<a :href="algorithmTerm.url" class="text-blue-500 hover:underline" rel="noopener" target="_blank">{{ algorithmTerm.id }}</a>)
    </h4>
    <p v-if="algorithmTerm.description" class="mt-1 text-neutral-700 whitespace-pre-wrap">
      {{ algorithmTerm.description }}
    </p>
  </div>
  <div v-else-if="isLoading" class="mt-2 text-sm text-neutral-500 flex items-center gap-2">
    <UIcon name="i-lucide-loader-2" class="w-4 h-4 animate-spin" />
    Loading algorithm details...
  </div>
  <div v-else-if="kisaoId" class="mt-2 text-sm text-neutral-700">
    <h4 class="font-bold">
      Executed algorithm: <a :href="`https://www.ebi.ac.uk/ols4/ontologies/kisao/terms?obo_id=${kisaoId.replace('_', ':')}`" class="text-blue-500 hover:underline" rel="noopener" target="_blank">{{ kisaoId }}</a>
    </h4>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'

const props = defineProps<{
  kisaoId?: string;
}>()

const algorithmTerm = ref<any>(null)
const isLoading = ref(false)
const runtimeConfig = useRuntimeConfig()

onMounted(async () => {
  if (props.kisaoId) {
    isLoading.value = true
    try {
      const res = await $fetch(`${runtimeConfig.public.legacy_api_url}/ontologies/KISAO/${props.kisaoId}`)
      algorithmTerm.value = res
    } catch (e) {
      console.error('Failed to fetch algorithm term', e)
    } finally {
      isLoading.value = false
    }
  }
})
</script>
