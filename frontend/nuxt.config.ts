// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  modules: [
    '@nuxt/eslint',
    '@nuxt/ui',
    '@nuxt/fonts',
    'lenis/nuxt',
    'nuxt-aos',
    '@nuxtjs/seo',
  ],

  devtools: {
    enabled: true
  },

  runtimeConfig: {
    public: {
      base_url: process.env.BASE_URL,
      api_url: process.env.API_URL,
      biosimulations_api_url: process.env.BIOSIMULATIONS_API_URL
    }
  },

  colorMode: {
    preference: 'light',
  },

  css: ['~/assets/css/main.css'],

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
