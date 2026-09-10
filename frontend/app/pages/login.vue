<script setup lang="ts">
import { useAuth0 } from '@auth0/auth0-vue'

const route = useRoute()
const config = useRuntimeConfig()
const { loginWithRedirect, isAuthenticated, user, logout } = useAuth0()

const forgotEmail = ref('')
const forgotStatus = ref<'idle' | 'sending' | 'sent'>('idle')

function login() {
  loginWithRedirect({
    appState: {
      target: (route.query.redirect as string) || '/'
    }
  })
}

function signup() {
  loginWithRedirect({
    authorizationParams: { screen_hint: 'signup' },
    appState: {
      target: (route.query.redirect as string) || '/'
    }
  })
}

async function submitForgotPassword() {
  if (!forgotEmail.value) return
  forgotStatus.value = 'sending'
  try {
    await $fetch(`https://${config.public.auth0Domain}/dbconnections/change_password`, {
      method: 'POST',
      body: {
        client_id: config.public.auth0ClientId,
        email: forgotEmail.value,
        connection: 'Username-Password-Authentication',
      },
    })
  } finally {
    // Always show the same generic message — don't reveal whether the email exists
    forgotStatus.value = 'sent'
  }
}

function resetForgotPassword() {
  forgotStatus.value = 'idle'
  forgotEmail.value = ''
}

function signOut() {
  logout({
    logoutParams: {
      returnTo: window.location.origin
    }
  })
}

useSeoMeta({
  title: 'Log In or Sign Up - BioSimulations',
  description: 'Log in or sign up via Auth0 to access BioSimulations platform features.'
})
</script>

<template>
  <div class="min-h-[calc(100vh-14rem)] flex flex-col justify-center py-12 px-4 sm:px-6 lg:px-8">
    <div class="w-full max-w-2xl mx-auto">
      <!-- Header -->
      <div class="text-center mb-8">
        <div class="inline-flex items-center justify-center mb-3">
          <UBadge color="primary" variant="subtle" size="md" class="gap-1.5 px-3 py-1 font-medium">
            <UIcon name="i-lucide-shield-check" class="w-4 h-4" />
            <span>Secure Authentication</span>
          </UBadge>
        </div>
        <h1 class="text-3xl font-bold tracking-tight text-gray-900 dark:text-white sm:text-4xl">
          Welcome to BioSimulations
        </h1>
        <p class="mt-2 text-base text-gray-600 dark:text-gray-400 max-w-md mx-auto">
          Log in to your account or create a new profile to run simulations and manage your projects.
        </p>
      </div>

      <!-- Authenticated Notice (if user visits while already logged in) -->
      <UAlert
        v-if="isAuthenticated"
        color="info"
        variant="subtle"
        icon="i-lucide-info"
        title="Already signed in"
        :description="`You are currently logged in as ${user?.name || user?.email || 'authenticated user'}.`"
        :actions="[
          { label: 'Go to Dashboard', to: '/', color: 'primary', variant: 'solid' },
          { label: 'Log Out', color: 'neutral', variant: 'outline', onClick: signOut }
        ]"
        class="mb-6"
      />

      <!-- Bento Grid -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4 sm:gap-6">
        <!-- Tile 1: Login -->
        <UCard
          class="flex flex-col justify-between transition-all duration-200 hover:shadow-md hover:border-primary-500/50"
          :ui="{ body: 'flex flex-col justify-between h-full p-6 sm:p-7 gap-6' }">
          <div class="space-y-4">
            <div class="flex items-center gap-4">
              <div class="w-12 h-12 rounded-xl bg-primary-50 text-primary-600 flex items-center justify-center ring-1 ring-primary-500/20">
                <UIcon name="i-lucide-log-in" class="w-6 h-6" />
              </div>
              <div>
                <h3 class="text-lg font-semibold text-gray-900">Log In</h3>
                <p class="text-sm text-gray-500 leading-relaxed">
                  Click below to login via Auth0.
                </p>
              </div>
            </div>
          </div>

          <UButton
            color="primary"
            size="lg"
            block
            icon="i-lucide-log-in"
            label="Login"
            @click="login"
            class="cursor-pointer justify-center font-medium"
          />
        </UCard>

        <!-- Tile 2: Sign Up -->
        <UCard
          class="flex flex-col justify-between transition-all duration-200 hover:shadow-md hover:border-primary-500/50"
          :ui="{ body: 'flex flex-col justify-between h-full p-6 sm:p-7 gap-6' }"
        >
          <div class="space-y-4">
            <div class="flex items-center gap-4">
              <div class="w-12 h-12 rounded-xl bg-primary-50 text-primary-600 flex items-center justify-center ring-1 ring-primary-500/20">
                <UIcon name="i-lucide-user-plus" class="w-6 h-6" />
              </div>
              <div>
                <h3 class="text-lg font-semibold text-gray-900">Sign Up</h3>
                <p class="text-sm text-gray-500 leading-relaxed">
                  Click below to sign up via Auth0.
                </p>
              </div>
            </div>
          </div>

          <div class="pt-2">
            <UButton
              color="primary"
              variant="outline"
              size="lg"
              block
              icon="i-lucide-user-plus"
              label="Sign Up"
              @click="signup"
              class="cursor-pointer justify-center font-medium"
            />
          </div>
        </UCard>

        <!-- Tile 3: Forgot Password Workflow (spans both columns on desktop) -->
        <UCard
          class="col-span-1 md:col-span-2 transition-all duration-200 hover:shadow-md hover:border-primary-500/40"
          :ui="{ body: 'p-6 sm:p-7' }"
        >
          <div class="flex flex-col gap-4">
            <div class="flex items-start gap-4">
              <div class="w-10 h-10 rounded-lg bg-neutral-100 text-neutral-600 flex items-center justify-center shrink-0 ring-1 ring-neutral-200">
                <UIcon name="i-lucide-key-round" class="w-5 h-5" />
              </div>
              <div class="flex-1 min-w-0">
                <h3 class="text-base font-semibold text-gray-900">Forgot Password</h3>
                <p class="text-sm text-gray-500 mt-0.5">
                  Enter your email and we'll send you a reset link.
                </p>
              </div>
            </div>

            <!-- Workflow: Input & Send -->
            <form
              v-if="forgotStatus !== 'sent'"
              @submit.prevent="submitForgotPassword"
              class="flex flex-col sm:flex-row gap-3 mt-1"
            >
              <UInput
                v-model="forgotEmail"
                type="email"
                placeholder="you@example.com"
                icon="i-lucide-mail"
                size="lg"
                required
                class="flex-1"
              />
              <UButton
                type="submit"
                :loading="forgotStatus === 'sending'"
                color="primary"
                :disabled="!forgotEmail"
                size="lg"
                icon="i-lucide-send"
                label="Send reset link"
                class="cursor-pointer shrink-0 justify-center font-medium"
              />
            </form>

            <!-- Workflow: Confirmation sent -->
            <div v-else class="space-y-3 mt-1">
              <UAlert
                color="success"
                variant="subtle"
                icon="i-lucide-circle-check"
                title="Reset link sent"
                description="If that email is registered, a reset link is on its way."
              />
              <div class="flex justify-end">
                <UButton
                  color="neutral"
                  variant="ghost"
                  size="sm"
                  icon="i-lucide-rotate-ccw"
                  label="Send another link"
                  @click="resetForgotPassword"
                  class="cursor-pointer"
                />
              </div>
            </div>
          </div>
        </UCard>
      </div>

      <!-- Trust Footer -->
      <div class="mt-8 flex items-center justify-center gap-2 text-xs text-gray-400 dark:text-gray-500 text-center">
        <UIcon name="i-lucide-shield" class="w-3.5 h-3.5 shrink-0" />
        <span>Authentication secured by Auth0.</span>
      </div>
    </div>
  </div>
</template>
