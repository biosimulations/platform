import { useAuth0 } from '@auth0/auth0-vue'

export default defineNuxtRouteMiddleware(async (to) => {
  // Auth0 browser state is unavailable during Nuxt SSR.
  if (import.meta.server) return

  const { isLoading, isAuthenticated } = useAuth0()

  // Fast path: if already authenticated, redirect immediately
  if (isAuthenticated.value) {
    let target = (to.query.redirect as string) || '/'
    if (target === to.path || target === '/login' || target === '/verified') {
      target = '/'
    }
    return navigateTo(target)
  }

  // If Auth0 is still checking cookies in the background, wait at most 400ms
  // so we don't freeze navigation if third-party cookies time out.
  if (isLoading.value) {
    await new Promise<void>((resolve) => {
      let stop: (() => void) | undefined
      const timer = setTimeout(() => {
        if (stop) stop()
        resolve()
      }, 400)

      stop = watch(isLoading, (loading) => {
        if (!loading) {
          clearTimeout(timer)
          if (stop) stop()
          resolve()
        }
      })
    })

    if (isAuthenticated.value) {
      let target = (to.query.redirect as string) || '/'
      if (target === to.path || target === '/login' || target === '/verified') {
        target = '/'
      }
      return navigateTo(target)
    }
  }
})
