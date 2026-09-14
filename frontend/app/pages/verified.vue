<script setup lang="ts">
import { useAuth0 } from '@auth0/auth0-vue'
import { DotLottieVue } from '@lottiefiles/dotlottie-vue'

const { isAuthenticated, loginWithRedirect, checkSession } = useAuth0()

// If authenticated in this browser session, refresh the session silently in the background
// so `user.email_verified` immediately reflects `true` without blocking page render.
onMounted(() => {
  if (isAuthenticated.value) {
    checkSession().catch((err) => {
      console.debug('Session sync on verified page skipped:', err)
    })
  }
})

function login() {
  loginWithRedirect({
    appState: {
      target: '/',
    },
  })
}

useSeoMeta({
  title: 'Email Verified - BioSimulations',
  description: 'Your email address has been successfully verified. Welcome to BioSimulations!',
})
</script>

<template>
  <div class="min-h-[calc(100vh-14rem)] flex items-center justify-center py-12 px-4 sm:px-6 lg:px-8">
    <UCard
      class="max-w-lg w-full text-center"
      :ui="{ body: 'p-8 sm:p-10 flex flex-col items-center gap-6' }"
    >
      <ClientOnly>
        <DotLottieVue class="w-[150px] aspect-square" loop autoplay src="/animations/confetti.lottie" />
        <template #fallback>
          <div class="w-[150px] aspect-square flex items-center justify-center">
            <UIcon name="i-lucide-badge-check" class="w-16 h-16 text-emerald-500" />
          </div>
        </template>
      </ClientOnly>

      <div class="space-y-3">
        <UBadge color="success" variant="subtle" size="md" class="gap-1 font-medium">
          <UIcon name="i-lucide-circle-check" class="w-3.5 h-3.5" />
          <span>Verification Complete</span>
        </UBadge>

        <h1 class="text-2xl sm:text-3xl font-bold tracking-tight text-gray-900 dark:text-white">
          Email Verified!
        </h1>

        <p class="text-base text-gray-600 dark:text-gray-300">
          Your email has been successfully verified. We hope you enjoy using BioSimulations!
        </p>
      </div>

      <!-- Action Buttons: handles both guest and authenticated users -->
      <div class="w-full space-y-2.5 pt-2">
        <UButton
          v-if="!isAuthenticated"
          @click="login"
          label="Login"
          icon="i-lucide-log-in"
          color="primary"
          size="lg"
          block
          class="cursor-pointer justify-center font-medium"
        />
        <div v-else class="flex flex-col sm:flex-row gap-2.5">
          <UButton
            to="/simulations/run"
            label="Run a Simulation"
            icon="i-lucide-play"
            color="primary"
            size="md"
            class="flex-1 cursor-pointer justify-center font-medium"
          />
          <UButton
            to="/profile"
            label="View Profile"
            icon="i-lucide-user"
            color="neutral"
            variant="outline"
            size="md"
            class="flex-1 cursor-pointer justify-center"
          />
        </div>
      </div>
    </UCard>
  </div>
</template>
