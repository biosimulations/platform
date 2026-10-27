<template>
  <div v-if="algorithmTerm" class="mt-2 text-sm">
    <h4 class="font-bold">
      Executed algorithm: {{ algorithmTerm.name || algorithmTerm.id }}
      <template v-if="algorithmTerm.id">
        (<a :href="algorithmTerm.url || `https://www.ebi.ac.uk/ols4/ontologies/kisao/terms?obo_id=${algorithmTerm.id.replace('_', ':')}`" class="text-blue-500 hover:underline" rel="noopener" target="_blank">{{ algorithmTerm.id }}</a>)
      </template>
    </h4>
    <p v-if="algorithmTerm.description" class="mt-1 text-neutral-700 whitespace-pre-wrap">
      {{ algorithmTerm.description }}
    </p>
  </div>
  <div v-else-if="isLoading" class="mt-2 text-sm text-neutral-500 flex items-center gap-2">
    <UIcon name="i-lucide-loader-2" class="w-4 h-4 animate-spin" />
    Loading algorithm details...
  </div>
  <div v-else-if="rawKisaoId" class="mt-2 text-sm text-neutral-700">
    <h4 class="font-bold">
      Executed algorithm: <a :href="`https://www.ebi.ac.uk/ols4/ontologies/kisao/terms?obo_id=${rawKisaoId.replace('_', ':')}`" class="text-blue-500 hover:underline" rel="noopener" target="_blank">{{ rawKisaoId }}</a>
    </h4>
  </div>
</template>

<script setup lang="ts">
import {computed, onMounted, ref} from 'vue'
import type {AlgorithmDetails} from '~/models/page'

const props = defineProps<{
  kisaoId?: string | AlgorithmDetails | any
  algorithm?: AlgorithmDetails | string | any
}>()

const algorithmTerm = ref<AlgorithmDetails | null>(null)
const isLoading = ref(false)
const runtimeConfig = useRuntimeConfig()

const rawKisaoId = computed<string | undefined>(() => {
  if (typeof props.kisaoId === 'string') return props.kisaoId
  if (typeof props.algorithm === 'string') return props.algorithm
  return undefined
})

onMounted(async () => {
  const directObj = (typeof props.algorithm === 'object' && props.algorithm)
    || (typeof props.kisaoId === 'object' && props.kisaoId)

  if (directObj && directObj.id) {
    algorithmTerm.value = directObj
    return
  }

  const idToFetch = rawKisaoId.value
  if (idToFetch) {
    isLoading.value = true
    try {
      algorithmTerm.value = await $fetch<AlgorithmDetails>(`${runtimeConfig.public.legacy_api_url}/ontologies/KISAO/${idToFetch}`)
    } catch (e) {
      console.error('Failed to fetch algorithm term', e)
    } finally {
      isLoading.value = false
    }
  }
})
</script>
