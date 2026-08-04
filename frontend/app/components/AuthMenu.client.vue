  <script setup lang="ts">
  import { useAuth0 } from '@auth0/auth0-vue'

  const route = useRoute()
  const { error, user, isAuthenticated, isLoading, loginWithRedirect, logout } = useAuth0()

  function login() {
    loginWithRedirect({
      appState: {
        target: route.fullPath
      }
    })
  }

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
      <USkeleton v-if="isLoading" class="h-8 w-20" />

      <UButton
        v-else-if="!isAuthenticated"
        label="Log in"
        icon="i-lucide-log-in"
        color="primary"
        @click="login"
      />

      <UDropdownMenu
        v-else
        :items="[[
          {
            label: user?.name || user?.email || 'Account',
            icon: 'i-lucide-user'
          },
          {
            label: 'My runs',
            icon: 'i-lucide-list',
            to: '/my-runs'
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
          icon="i-lucide-user-circle"
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
