  import { useAuth0 } from '@auth0/auth0-vue'

  export default defineNuxtRouteMiddleware(async (to) => {
    // Auth0 browser state is unavailable during Nuxt SSR.
    if (import.meta.server) return

    const { isLoading, isAuthenticated, loginWithRedirect } = useAuth0()

    // Wait for the SDK to finish restoring an existing login session (with safety timeout)
    if (isLoading.value) {
      await new Promise<void>((resolve) => {
        let stop: (() => void) | undefined
        const timer = setTimeout(() => {
          if (stop) stop()
          resolve()
        }, 1500)

        stop = watch(isLoading, (loading) => {
          if (!loading) {
            clearTimeout(timer)
            if (stop) stop()
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
