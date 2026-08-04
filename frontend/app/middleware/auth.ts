  import { useAuth0 } from '@auth0/auth0-vue'

  export default defineNuxtRouteMiddleware(async (to) => {
    // Auth0 browser state is unavailable during Nuxt SSR.
    if (import.meta.server) return

    const { isLoading, isAuthenticated, loginWithRedirect } = useAuth0()

    // Wait for the SDK to finish restoring an existing login session.
    if (isLoading.value) {
      await new Promise<void>((resolve) => {
        const stop = watch(isLoading, (loading) => {
          if (!loading) {
            stop()
            resolve()
          }
        })
      })
    }

    if (!isAuthenticated.value) {
      await loginWithRedirect({
        appState: { target: to.fullPath }
      })

      return abortNavigation()
    }
  })
