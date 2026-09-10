<script setup lang="ts">
import { useAuth0 } from '@auth0/auth0-vue'

const config = useRuntimeConfig()
const {
  user,
  isLoading,
  checkSession,
  getAccessTokenSilently,
  logout,
} = useAuth0()

// Form states: Change Email
const newEmail = ref('')
const emailStatus = ref<'idle' | 'loading' | 'success' | 'error'>('idle')
const emailErrorMessage = ref('')
const emailSuccessMessage = ref('')

// Form states: Change Name
const newName = ref('')
const nameStatus = ref<'idle' | 'loading' | 'success' | 'error'>('idle')
const nameErrorMessage = ref('')
const nameSuccessMessage = ref('')

// Form states: Password Reset Link
const passwordResetStatus = ref<'idle' | 'loading' | 'success' | 'error'>('idle')
const passwordResetMessage = ref('')

// Form states: Resend Verification Email
const verificationStatus = ref<'idle' | 'loading' | 'success' | 'error'>('idle')
const verificationMessage = ref('')

// Initialize name and email when user data is loaded
watch(
  user,
  (currentUser) => {
    if (currentUser) {
      if (!newName.value && currentUser.name) {
        newName.value = currentUser.name
      }
      if (!newEmail.value && currentUser.email) {
        newEmail.value = currentUser.email
      }
    }
  },
  { immediate: true },
)

// Check whether account uses social login (e.g. Google) or password login
const isSocialLogin = computed(() => {
  if (!user.value?.sub) return false
  return !user.value.sub.startsWith('auth0|')
})

const socialProviderName = computed(() => {
  if (!user.value?.sub) return 'an external provider'
  const prefix = user.value.sub.split('|')[0]
  if (prefix === 'google-oauth2') return 'Google'
  if (prefix === 'github') return 'GitHub'
  return prefix
})

// Action: Send Password Reset Link
async function sendPasswordResetLink() {
  if (!user.value?.email) return
  passwordResetStatus.value = 'loading'
  passwordResetMessage.value = ''

  try {
    await $fetch(`https://${config.public.auth0Domain}/dbconnections/change_password`, {
      method: 'POST',
      body: {
        client_id: config.public.auth0ClientId,
        email: user.value.email,
        connection: 'Username-Password-Authentication',
      },
    })
    passwordResetStatus.value = 'success'
    passwordResetMessage.value = `A password reset link has been sent to ${user.value.email}. Please check your inbox.`
  } catch (err: any) {
    console.error('Failed to send password reset email:', err)
    passwordResetStatus.value = 'error'
    passwordResetMessage.value = err?.data?.message || err?.message || 'Failed to send password reset email. Please try again later.'
  }
}

// Action: Resend Verification Email
async function resendVerificationEmail() {
  if (!user.value?.email) return
  verificationStatus.value = 'loading'
  verificationMessage.value = ''

  try {
    const token = await getAccessTokenSilently({
      authorizationParams: {
        audience: config.public.auth0Audience,
      },
    })

    const apiBase = config.public.api_url || ''
    await $fetch(`${apiBase}/api/v1/me/resend-verification`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
      },
    })

    verificationStatus.value = 'success'
    verificationMessage.value = `A verification email has been sent to ${user.value.email}. Follow the link in the message to complete verification.`
  } catch (err: any) {
    console.error('Failed to resend verification email:', err)
    verificationStatus.value = 'error'
    verificationMessage.value = err?.data?.detail || err?.message || 'Unable to send verification email automatically. Please check your spam folder or contact support if you need assistance.'
  }
}

// Action: Change Email
async function submitChangeEmail() {
  if (!newEmail.value || newEmail.value === user.value?.email) return
  emailStatus.value = 'loading'
  emailErrorMessage.value = ''
  emailSuccessMessage.value = ''

  try {
    const token = await getAccessTokenSilently({
      authorizationParams: {
        audience: config.public.auth0Audience,
      },
    })

    const apiBase = config.public.api_url || ''
    await $fetch(`${apiBase}/api/v1/me`, {
      method: 'PATCH',
      headers: {
        Authorization: `Bearer ${token}`,
      },
      body: {
        email: newEmail.value,
      },
    })

    emailStatus.value = 'success'
    emailSuccessMessage.value = `Email updated to ${newEmail.value}. A verification email has been sent to the new address.`
    // Silently refresh user info in the background
    try {
      await checkSession()
    } catch {
      // Non-blocking
    }
  } catch (err: any) {
    console.error('Failed to change email:', err)
    emailStatus.value = 'error'
    emailErrorMessage.value = err?.data?.detail || err?.message || 'Failed to update email address. Please make sure the email is valid and not already in use.'
  }
}

// Action: Update Profile Name
async function submitUpdateName() {
  if (!newName.value || newName.value === user.value?.name) return
  nameStatus.value = 'loading'
  nameErrorMessage.value = ''
  nameSuccessMessage.value = ''

  try {
    const token = await getAccessTokenSilently({
      authorizationParams: {
        audience: config.public.auth0Audience,
      },
    })

    const apiBase = config.public.api_url || ''
    await $fetch(`${apiBase}/api/v1/me`, {
      method: 'PATCH',
      headers: {
        Authorization: `Bearer ${token}`,
      },
      body: {
        name: newName.value,
      },
    })

    nameStatus.value = 'success'
    nameSuccessMessage.value = 'Your name has been updated.'
    // Silently refresh user info in the background
    try {
      await checkSession()
    } catch {
      // Non-blocking
    }
  } catch (err: any) {
    console.error('Failed to update name:', err)
    nameStatus.value = 'error'
    nameErrorMessage.value = err?.data?.detail || err?.message || 'Failed to update name.'
  }
}

// Action: Sign Out
function signOut() {
  logout({
    logoutParams: {
      returnTo: window.location.origin,
    },
  })
}

useSeoMeta({
  title: 'Account Settings - BioSimulations',
  description: 'Manage your BioSimulations account details, email, and password.',
})
</script>

<template>
  <div class="py-8 px-4 sm:px-6 lg:px-8 max-w-5xl mx-auto space-y-5">
    <!-- Loading State -->
    <div v-if="isLoading" class="space-y-4">
      <USkeleton class="h-28 w-full rounded-2xl" />
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4 sm:gap-5">
        <USkeleton class="h-48 rounded-2xl" />
        <USkeleton class="h-48 rounded-2xl" />
      </div>
    </div>

    <!-- Authenticated Account Content -->
    <template v-else>
      <!-- Unverified Email Alert Banner -->
      <UAlert
        v-if="user && !user.email_verified"
        color="warning"
        variant="subtle"
        icon="i-lucide-alert-triangle"
        title="Email verification needed"
        description="Your email address has not been verified yet. Verifying your email ensures you receive simulation notifications and allows you to publish projects."
        :actions="[
          {
            label: 'Resend Verification Email',
            color: 'warning',
            variant: 'solid',
            loading: verificationStatus === 'loading',
            onClick: resendVerificationEmail,
          },
        ]"
      />

      <!-- Verification feedback messages -->
      <UAlert
        v-if="verificationStatus === 'success'"
        color="success"
        variant="subtle"
        icon="i-lucide-circle-check"
        title="Verification email sent"
        :description="verificationMessage"
        close
        @close="verificationStatus = 'idle'"
      />
      <UAlert
        v-else-if="verificationStatus === 'error'"
        color="error"
        variant="subtle"
        icon="i-lucide-circle-alert"
        title="Unable to send email"
        :description="verificationMessage"
        close
        @close="verificationStatus = 'idle'"
      />

      <!-- Profile Header Card (Slimmer) -->
      <UCard
        class="overflow-hidden"
        :ui="{ body: 'p-4 sm:p-5' }"
      >
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div class="flex items-center gap-3.5">
            <div class="relative shrink-0">
              <img
                v-if="user?.picture"
                :src="user.picture"
                :alt="user.name || 'Profile Picture'"
                class="w-12 h-12 sm:w-14 sm:h-14 rounded-lg object-cover"
              />
              <div
                v-else
                class="w-12 h-12 sm:w-14 sm:h-14 rounded-xl bg-primary-100 dark:bg-primary-950/60 text-primary-600 dark:text-primary-400 flex items-center justify-center text-xl font-bold ring-2 ring-primary-500/20"
              >
                {{ (user?.name || user?.email || 'U').charAt(0).toUpperCase() }}
              </div>
            </div>

            <div class="space-y-0.5">
              <div class="flex items-center gap-2 flex-wrap">
                <h1 class="text-lg sm:text-xl font-bold text-gray-900 dark:text-white">
                  {{ user?.name || user?.nickname || 'BioSimulations User' }}
                </h1>
                <UBadge
                  v-if="user?.email_verified"
                  color="success"
                  variant="subtle"
                  size="sm"
                  class="gap-1 font-medium"
                >
                  <UIcon name="i-lucide-badge-check" class="w-3.5 h-3.5" />
                  <span>Verified</span>
                </UBadge>
                <UBadge
                  v-else
                  color="warning"
                  variant="subtle"
                  size="sm"
                  class="gap-1 font-medium"
                >
                  <UIcon name="i-lucide-clock" class="w-3.5 h-3.5" />
                  <span>Pending Verification</span>
                </UBadge>
              </div>

              <p class="text-xs text-gray-500 dark:text-gray-400">
                {{ user?.email }}
              </p>
            </div>
          </div>

          <div class="flex items-center gap-2 shrink-0">
            <UButton
              color="neutral"
              variant="outline"
              size="sm"
              icon="i-lucide-log-out"
              label="Log Out"
              @click="signOut"
              class="cursor-pointer"
            />
          </div>
        </div>
      </UCard>

      <!-- Bento Grid for Routine Self-Serve Account Actions -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4 sm:gap-5">
        <!-- Tile 1: Personal Information (Name) -->
        <UCard
          class="flex flex-col justify-between transition-all duration-200 hover:shadow-sm"
          :ui="{ body: 'p-4 sm:p-5 flex flex-col justify-between h-full gap-3.5' }"
        >
          <div class="space-y-3">
            <div class="flex items-start gap-3.5">
              <div class="w-10 h-10 rounded-xl bg-purple-50 dark:bg-purple-950/60 text-purple-600 dark:text-purple-400 flex items-center justify-center ring-1 ring-purple-500/20 shrink-0">
                <UIcon name="i-lucide-user" class="w-5 h-5" />
              </div>
              <div class="min-w-0 flex-1">
                <h3 class="text-base font-semibold text-gray-900 dark:text-white">Personal Information</h3>
                <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5 leading-relaxed">
                  Your name as it appears on simulation runs and authored project models.
                </p>
              </div>
            </div>

            <form @submit.prevent="submitUpdateName" class="space-y-2.5 pt-1">
              <UFormField label="Full Name">
                <UInput
                  v-model="newName"
                  placeholder="Your Name"
                  icon="i-lucide-user"
                  size="md"
                  class="w-full"
                />
              </UFormField>

              <UAlert
                v-if="nameStatus === 'success'"
                color="success"
                variant="subtle"
                icon="i-lucide-circle-check"
                :description="nameSuccessMessage"
                close
                @close="nameStatus = 'idle'"
              />
              <UAlert
                v-else-if="nameStatus === 'error'"
                color="error"
                variant="subtle"
                icon="i-lucide-circle-alert"
                :description="nameErrorMessage"
                close
                @close="nameStatus = 'idle'"
              />

              <div class="pt-1">
                <UButton
                  type="submit"
                  label="Save Name"
                  icon="i-lucide-check"
                  color="primary"
                  size="md"
                  block
                  :loading="nameStatus === 'loading'"
                  :disabled="!newName || newName === user?.name"
                  class="cursor-pointer justify-center font-medium"
                />
              </div>
            </form>
          </div>
        </UCard>

        <!-- Tile 2: Change Email Address -->
        <UCard
          class="flex flex-col justify-between transition-all duration-200 hover:shadow-sm"
          :ui="{ body: 'p-4 sm:p-5 flex flex-col justify-between h-full gap-3.5' }"
        >
          <div class="space-y-3">
            <div class="flex items-start gap-3.5">
              <div class="w-10 h-10 rounded-xl bg-blue-50 dark:bg-blue-950/60 text-blue-600 dark:text-blue-400 flex items-center justify-center ring-1 ring-blue-500/20 shrink-0">
                <UIcon name="i-lucide-mail" class="w-5 h-5" />
              </div>
              <div class="min-w-0 flex-1">
                <h3 class="text-base font-semibold text-gray-900 dark:text-white">Email Address</h3>
                <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5 leading-relaxed">
                  Update your account email address. A confirmation link will be sent to verify the new address.
                </p>
              </div>
            </div>

            <form @submit.prevent="submitChangeEmail" class="space-y-2.5 pt-1">
              <UFormField label="New Email Address" required>
                <UInput
                  v-model="newEmail"
                  type="email"
                  placeholder="name@institution.edu"
                  icon="i-lucide-at-sign"
                  size="md"
                  class="w-full"
                />
              </UFormField>

              <UAlert
                v-if="emailStatus === 'success'"
                color="success"
                variant="subtle"
                icon="i-lucide-circle-check"
                :description="emailSuccessMessage"
                close
                @close="emailStatus = 'idle'"
              />
              <UAlert
                v-else-if="emailStatus === 'error'"
                color="error"
                variant="subtle"
                icon="i-lucide-circle-alert"
                :description="emailErrorMessage"
                close
                @close="emailStatus = 'idle'"
              />

              <div class="pt-1">
                <UButton
                  type="submit"
                  label="Update Email"
                  icon="i-lucide-send"
                  color="neutral"
                  variant="subtle"
                  size="md"
                  block
                  :loading="emailStatus === 'loading'"
                  :disabled="!newEmail || newEmail === user?.email"
                  class="cursor-pointer justify-center font-medium"
                />
              </div>
            </form>
          </div>
        </UCard>

        <!-- Tile 3: Password & Access -->
        <UCard
          class="flex flex-col justify-between transition-all duration-200 hover:shadow-sm"
          :ui="{ body: 'p-4 sm:p-5 flex flex-col justify-between h-full gap-3.5' }"
        >
          <div class="space-y-3">
            <div class="flex items-start gap-3.5">
              <div class="w-10 h-10 rounded-xl bg-amber-50 dark:bg-amber-950/60 text-amber-600 dark:text-amber-400 flex items-center justify-center ring-1 ring-amber-500/20 shrink-0">
                <UIcon name="i-lucide-key-round" class="w-5 h-5" />
              </div>
              <div class="min-w-0 flex-1">
                <h3 class="text-base font-semibold text-gray-900 dark:text-white">Password & Security</h3>
                <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5 leading-relaxed">
                  Change or reset your password through a secure verification link sent to your email.
                </p>
              </div>
            </div>

            <div class="space-y-2.5 pt-1">
              <div v-if="isSocialLogin" class="p-3 bg-neutral-50 dark:bg-neutral-800/60 rounded-lg text-xs text-neutral-600 dark:text-neutral-400 flex items-start gap-2">
                <UIcon name="i-lucide-info" class="w-4 h-4 shrink-0 text-neutral-500 mt-0.5" />
                <span>
                  You sign in via <strong>{{ socialProviderName }}</strong>. Password management is handled directly through your {{ socialProviderName }} account.
                </span>
              </div>
              <div v-else class="text-xs text-gray-500 dark:text-gray-400">
                We'll send a secure password reset link to <strong>{{ user?.email }}</strong>.
              </div>

              <UAlert
                v-if="passwordResetStatus === 'success'"
                color="success"
                variant="subtle"
                icon="i-lucide-circle-check"
                :description="passwordResetMessage"
                close
                @close="passwordResetStatus = 'idle'"
              />
              <UAlert
                v-else-if="passwordResetStatus === 'error'"
                color="error"
                variant="subtle"
                icon="i-lucide-circle-alert"
                :description="passwordResetMessage"
                close
                @close="passwordResetStatus = 'idle'"
              />

              <div class="pt-1">
                <UButton
                  v-if="!isSocialLogin"
                  label="Send Password Reset Link"
                  icon="i-lucide-lock"
                  color="neutral"
                  variant="subtle"
                  size="md"
                  block
                  :loading="passwordResetStatus === 'loading'"
                  @click="sendPasswordResetLink"
                  class="cursor-pointer justify-center font-medium"
                />
              </div>
            </div>
          </div>
        </UCard>

        <!-- Tile 4: Email Verification -->
        <UCard
          class="flex flex-col justify-between transition-all duration-200 hover:shadow-sm"
          :ui="{ body: 'p-4 sm:p-5 flex flex-col justify-between h-full gap-3.5' }"
        >
          <div class="space-y-3">
            <div class="flex items-start gap-3.5">
              <div class="w-10 h-10 rounded-xl bg-emerald-50 dark:bg-emerald-950/60 text-emerald-600 dark:text-emerald-400 flex items-center justify-center ring-1 ring-emerald-500/20 shrink-0">
                <UIcon name="i-lucide-shield-check" class="w-5 h-5" />
              </div>
              <div class="min-w-0 flex-1">
                <h3 class="text-base font-semibold text-gray-900 dark:text-white">Email Verification</h3>
                <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5 leading-relaxed">
                  Verification confirms your identity and ensures simulation status notifications reach you.
                </p>
              </div>
            </div>

            <div class="space-y-2.5 pt-1">
              <div class="flex items-center gap-3 p-2.5 rounded-lg bg-neutral-50 dark:bg-neutral-800/60">
                <UIcon
                  :name="user?.email_verified ? 'i-lucide-check-circle-2' : 'i-lucide-alert-circle'"
                  :class="user?.email_verified ? 'text-emerald-500' : 'text-amber-500'"
                  class="w-4.5 h-4.5 shrink-0"
                />
                <div class="text-xs">
                  <div class="font-medium text-gray-900 dark:text-white">
                    {{ user?.email_verified ? 'Email is Verified' : 'Verification Needed' }}
                  </div>
                  <div class="text-gray-500 dark:text-gray-400 text-[11px]">
                    {{ user?.email }}
                  </div>
                </div>
              </div>

              <div class="pt-1">
                <UButton
                  v-if="!user?.email_verified"
                  label="Resend Verification Email"
                  icon="i-lucide-mail-plus"
                  color="primary"
                  variant="outline"
                  size="md"
                  block
                  :loading="verificationStatus === 'loading'"
                  @click="resendVerificationEmail"
                  class="cursor-pointer justify-center font-medium"
                />
                <div v-else class="text-center text-xs text-emerald-600 dark:text-emerald-400 font-medium py-2 flex items-center justify-center gap-1.5">
                  <UIcon name="i-lucide-check" class="w-4 h-4" />
                  <span>Your email is verified and ready for simulation runs</span>
                </div>
              </div>
            </div>
          </div>
        </UCard>
      </div>
    </template>
  </div>
</template>
