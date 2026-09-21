import { createAuth0 } from '@auth0/auth0-vue'

export default defineNuxtPlugin((nuxtApp) => {
  const config = useRuntimeConfig()
  const auth0 = createAuth0({
    domain: config.public.auth0Domain,
    clientId: config.public.auth0ClientId,
    useRefreshTokens: true,
    cacheLocation: 'localstorage',
    authorizationParams: {
      redirect_uri: window.location.origin,
      audience: config.public.auth0Audience,
    },
  })
  nuxtApp.vueApp.use(auth0)

  // Intercept all outgoing HTTP requests: enable credentials and attach Bearer token when logged in
  globalThis.$fetch = $fetch.create({
    async onRequest({ options }) {
      options.credentials = 'include'

      if (auth0.isAuthenticated.value) {
        try {
          const token = await auth0.getAccessTokenSilently({
            authorizationParams: {
              audience: config.public.auth0Audience,
            },
          })
          if (token) {
            const headers = new Headers(options.headers)
            if (!headers.has('Authorization')) {
              headers.set('Authorization', `Bearer ${token}`)
            }
            options.headers = headers
          }
        } catch {
          // Non-fatal if token retrieval fails; proceeds unauthenticated
        }
      }
    },
  })
})
