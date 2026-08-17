// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  modules: [
    '@nuxt/eslint',
    '@nuxt/ui',
    '@nuxt/fonts',
    'lenis/nuxt',
    'nuxt-aos',
    '@nuxtjs/seo',
    '@nuxt/image',
    'nuxt-easy-lightbox',
    'nuxt-og-image'
  ],

  devtools: {
    enabled: true
  },

  css: ['~/assets/css/main.css'],

  site: {
    url: 'https://biosimulations.org',
    name: 'BioSimulations',
    description: 'Web application for sharing dynamical models of biological systems and visualizing their results',
    defaultLocale: 'en'
  },

  colorMode: {
    preference: 'light'
  },

  runtimeConfig: {
    // Server-only: in-cluster URL the Nitro server uses for SSR-time fetches
    // to the backend, so SSR traffic skips the public ingress, TLS, and DNS.
    // Read via useRuntimeConfig().apiUrl on the server; not exposed to the
    // browser. Defaults to API_URL if API_URL_INTERNAL is unset, so dev
    // setups that don't have a separate internal URL still work.
    apiUrl: process.env.API_URL_INTERNAL || process.env.API_URL,
    public: {
      base_url: process.env.BASE_URL,
      api_url: process.env.API_URL,
      biosimulations_api_url: process.env.BIOSIMULATIONS_API_URL,
      legacy_api_url: process.env.LEGACY_API_URL,
      auth0Domain: process.env.NUXT_PUBLIC_AUTH0_DOMAIN,
      auth0ClientId: process.env.NUXT_PUBLIC_AUTH0_CLIENT_ID,
      auth0Audience: process.env.NUXT_PUBLIC_AUTH0_AUDIENCE
    }
  },

  routeRules: {
    '/': { prerender: true },
    '/projects': { redirect: '/biosim-db' },
    '/runs': { redirect: '/simulations' },
    // Client-only. The Auth0 SDK is installed by plugins/auth0.client.ts, which
    // cannot run on the server (it reads window.location.origin for the
    // redirect_uri), so useAuth0() is undefined during SSR and login.vue's
    // top-level destructure throws a 500. The page is a redirect launcher with
    // nothing to server-render anyway. Only direct hits and refreshes were
    // affected -- in-app navigation to /login is client-side and always worked.
    '/login': { ssr: false }
  },

  compatibilityDate: '2025-01-15',

  vite: {
    optimizeDeps: {
      exclude: ["plotly.js-dist-min"],
    },
  },

  eslint: {
    config: {
      stylistic: {
        commaDangle: 'never',
        braceStyle: '1tbs'
      }
    }
  },
})
