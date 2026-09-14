import { useAuth0 } from '@auth0/auth0-vue'
import { until } from '@vueuse/core'

export interface UserRouteRule {
  target: string
  redirect?: string
}

export type RouteRuleConfig = string | UserRouteRule

/**
 * Routes requiring the user to be signed in.
 * If an unauthenticated user visits `target`, they are redirected to `redirect?redirect=<target>`.
 */
export const authenticatedRoutes: RouteRuleConfig[] = [
  { target: '/profile', redirect: '/login' },
]

/**
 * Routes reserved for unauthenticated guests.
 * If a signed-in user visits `target`, they are redirected to `to.query.redirect` or `redirect`.
 */
export const guestOnlyRoutes: RouteRuleConfig[] = [
  { target: '/login', redirect: '/' },
]

function normalizeRule(item: RouteRuleConfig, defaultRedirect: string) {
  if (typeof item === 'string') {
    return { target: item, redirect: defaultRedirect }
  }
  return { target: item.target, redirect: item.redirect || defaultRedirect }
}

export default defineNuxtRouteMiddleware(async (to) => {
  const authRules = authenticatedRoutes.map(r => normalizeRule(r, '/login'))
  const guestRules = guestOnlyRoutes.map(r => normalizeRule(r, '/'))

  const authMatch = authRules.find(r => to.path === r.target || to.path.startsWith(`${r.target}/`))
  const guestMatch = guestRules.find(r => to.path === r.target || to.path.startsWith(`${r.target}/`))

  // Fast path: if current route matches neither list, skip immediately (public route)
  if (!authMatch && !guestMatch) {
    return
  }

  // Auth0 browser state is unavailable during Nuxt SSR.
  if (import.meta.server) return

  const { isLoading, isAuthenticated } = useAuth0()

  // Reactively wait for Auth0 to finish resolving session state from localStorage
  if (isLoading.value) {
    await until(isLoading).toBe(false)
  }

  // Case 1: Guest-only route (e.g. /login) -> Authenticated users are redirected away
  if (guestMatch) {
    if (isAuthenticated.value) {
      const destination = (to.query.redirect as string) || guestMatch.redirect
      const isLoop = destination === to.path || guestRules.some(r => destination === r.target)
      return navigateTo(isLoop ? guestMatch.redirect : destination)
    }
    return
  }

  // Case 2: Protected route (e.g. /profile) -> Unauthenticated visitors are redirected
  if (authMatch) {
    if (!isAuthenticated.value) {
      return navigateTo({
        path: authMatch.redirect,
        query: { redirect: to.fullPath },
      })
    }
  }
})
