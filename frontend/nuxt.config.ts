// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  modules: [
    '@nuxt/eslint',
    '@nuxt/ui',
    '@nuxt/fonts',
    'lenis/nuxt',
    'nuxt-aos',
    '@nuxtjs/seo',
    '@nuxt/image'
  ],

  devtools: {
    enabled: true
  },

  css: ['~/assets/css/main.css'],

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
    }
  },

  routeRules: {
    '/': { prerender: true }
  },

  compatibilityDate: '2025-01-15',

  eslint: {
    config: {
      stylistic: {
        commaDangle: 'never',
        braceStyle: '1tbs'
      }
    }
  }
})