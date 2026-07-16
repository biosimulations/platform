<script setup lang="ts">
  import {DotLottieVue} from "@lottiefiles/dotlottie-vue";

  defineProps<{
    title: string
  }>()

  const toast = useToast()
  const message: any = useState('message')

  if (!message.value) {
    navigateTo('/')
  }

  function copy() {
    navigator.clipboard.writeText(message.value)
    toast.add({title: 'Successfully copied error message to clipboard'})
  }
</script>

<template>
  <section class="w-full min-h-[calc(100vh-var(--ui-header-height))] px-6 max-w-[1200px] mx-auto flex flex-col gap-4 items-center justify-center text-center md:text-left">
    <div class="w-full md:w-max md:max-w-[700px] lg:max-w-[900px] flex flex-col items-center justify-center gap-2">
      <DotLottieVue class="w-[150px] aspect-square" autoplay src="/animations/error.lottie" />
      <h1 class="text-2xl font-bold md:whitespace-nowrap">Whoops, an error occurred:</h1>
      <p class="text-center p-2 border border-gray-200 bg-neutral-50 rounded font-mono">{{message}}</p>

      <div class="w-max flex flex-col gap-3 mt-2">
        <UButton color="primary" variant="outline" class="cursor-pointer justify-center flex-1" icon="i-lucide-copy" label="Copy Error Message" @click="copy()"></UButton>

        <div class="w-full flex-1 flex items-center justify-center gap-3">
          <UButton color="primary" class="cursor-pointer" to="/" icon="i-lucide-home" label="Go Home"></UButton>
          <UButton color="neutral" class="cursor-pointer" to="/support" variant="outline" target="_blank" icon="i-fluent-chat-help-20-regular" label="Contact Support"></UButton>
        </div>
      </div>
    </div>
  </section>
</template>
