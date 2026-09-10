  <script setup lang="ts">
  import { useAuth0 } from '@auth0/auth0-vue'

  const { error, user, isAuthenticated, isLoading, logout } = useAuth0()

  function signOut() {
    logout({
      logoutParams: {
        returnTo: window.location.origin
      }
    })
  }
  </script>

  <template>
    <div class="flex items-center gap-2">
      <p class="opacity-40 font-light">|</p>
      <USkeleton v-if="isLoading" class="h-8 w-20" />

      <UButton
        v-else-if="!isAuthenticated"
        label="Login / Register"
        variant="ghost"
        leading-icon="i-lucide-user"
        color="neutral"
        class="cursor-pointer"
        to="/login"
      />

      <UDropdownMenu
        v-else
        :items="[[
          {
            label: 'Profile',
            icon: 'i-lucide-user',
            to: '/profile'
          },
          {
            label: 'My Runs',
            icon: 'i-lucide-list',
            to: '/simulations'
          },
          {
            label: 'Log out',
            icon: 'i-lucide-log-out',
            onSelect: signOut
          }
        ]]"
      >
        <UButton
          :label="user?.name || user?.email || 'Account'"
          leading-icon="i-lucide-user-circle"
          trailing-icon="i-lucide-chevron-down"
          color="neutral"
          variant="ghost"
        />
      </UDropdownMenu>

      <UAlert
        v-if="error"
        color="error"
        icon="i-lucide-circle-alert"
        :description="error.message"
        title="Unable to complete sign-in"
      />
    </div>
  </template>
