<script setup lang="ts">
import type { NavigationMenuItem } from '@nuxt/ui'

useHead({
  meta: [
    { name: 'viewport', content: 'width=device-width, initial-scale=1' }
  ],
  link: [
    { rel: 'icon', href: '/favicon.ico' }
  ],
  htmlAttrs: {
    lang: 'en'
  }
})

const title = 'BioSimulations'
const description = 'A production-ready starter template powered by Nuxt UI. Build beautiful, accessible, and performant applications in minutes, not hours.'

useSeoMeta({
  title,
  description,
  ogTitle: title,
  ogDescription: description,
  ogImage: 'https://ui.nuxt.com/assets/templates/nuxt/starter-light.png',
  twitterImage: 'https://ui.nuxt.com/assets/templates/nuxt/starter-light.png',
  twitterCard: 'summary_large_image'
})

const navigation_items = ref<NavigationMenuItem[][]>([
  [
    {
      label: 'BioSim DB',
      icon: 'i-lucide-database',
      to: '/biosim-db'
    },
    {
      label: 'Simulations',
      // icon: 'i-lucide-bar-chart-3',
      class: 'cursor-pointer',
      children: [
        {
          label: 'Run a Simulation',
          // description: 'Pick a simulator and provide data to run a simulation.',
          icon: 'i-fluent-sparkle-20-filled',
          to: '/simulations/run',
          class: 'cursor-pointer'
        },
        {
          label: 'Browse Simulations',
          // description: 'Browse over 100,000 published simulations.',
          icon: 'i-lucide-search',
          to: '/simulations',
          class: 'cursor-pointer'
        },
        {
          label: 'Validate a simulation',
          icon: 'i-lucide-activity',
          to: '/utilities/validate-simulation',
          class: 'cursor-pointer'
        }
      ]
    },
    {
      label: 'Simulators',
      class: 'cursor-pointer',
      children: [
        {
          label: 'Get Started',
          icon: 'i-lucide-monitor-cog',
          to: '/simulators/get-started'
        },
        {
          label: 'Browse Simulators',
          icon: 'i-lucide-search',
          to: '/simulators'
        },
        {
          label: 'Suggest a simulator',
          icon: 'i-lucide-lightbulb',
          to: '/simulators/suggest',
          class: 'cursor-pointer'
        },
        {
          label: 'Validate a simulator',
          icon: 'i-lucide-cpu',
          to: '/simulators/validate',
          class: 'cursor-pointer'
        }
      ]
    },
    {
      label: 'Utilities',
      // icon: 'i-lucide-wrench',
      children: [
        {
          label: 'New Simulation Experiment',
          icon: 'i-lucide-folder-plus',
          to: '/utilities/new-simulation-experiment',
          class: 'cursor-pointer'
        },
        {
          label: 'Validate a model',
          icon: 'i-lucide-file-check',
          to: '/utilities/validate-model',
          class: 'cursor-pointer'
        },
        {
          label: 'Validate metadata',
          icon: 'i-lucide-tags',
          to: '/utilities/validate-metadata',
          class: 'cursor-pointer'
        },
        {
          label: 'Validate a project',
          icon: 'i-lucide-folder-check',
          to: '/utilities/validate-project',
          class: 'cursor-pointer'
        },
        {
          label: 'Describe visualizations',
          icon: 'i-lucide-bar-chart-3',
          to: 'utilities/describe-visualizations',
          class: 'cursor-pointer'
        },
        {
          label: 'BioSimulations REST API',
          icon: 'i-lucide-server',
          to: 'https://api.biosimulations.org',
          class: 'cursor-pointer'
        },
        {
          label: 'BioSimulators REST API',
          icon: 'i-lucide-database',
          to: 'https://api.biosimulators.org',
          class: 'cursor-pointer'
        },
        {
          label: 'Verification REST API (new)',
          icon: 'i-lucide-shield-check',
          to: 'https://biochecknet.biosimulations.org/docs',
          class: 'cursor-pointer'
        }
      ]
    },
    {
      label: 'Learn',
      // icon: 'i-lucide-book-open',
      to: '',
      class: 'cursor-pointer'
    }
  ],
  [
    {
      label: 'GitHub',
      icon: 'i-simple-icons-github',
      class: 'cursor-pointer',
      badge: {
        icon: 'i-lucide-star',
        label: '42'
      },
      to: 'https://github.com/biosimulations/biosimulations',
      target: '_blank'
    }
  ]
])

const lenisOptions = {
  // autoRaf: false,
  // lenis options (optional)
}

onMounted(() => {
  useLenis((_lenis) => {
    // called every scroll
    // console.log(_lenis)
  })
})
</script>

<template>
  <VueLenis
    root
    :options="lenisOptions"
  />
  <UApp>
    <UHeader
      class="w-max-[1200px] mx-auto"
    >
      <template #title>
        <NuxtLink to="/">
          <img
            class="w-auto h-8 shrink-0"
            src="/images/full_logo_color.svg"
            alt="BioSimulatiosn Logo - Bio and Simulations are bisected by a circular icon of two arrows interwoven like a DNA molecule"
          >
        </NuxtLink>
      </template>

      <!--      <UNavigationMenu class="hidden md:flex" orientation="horizontal" :items="navigation_items"></UNavigationMenu> -->

      <template #right>
        <UNavigationMenu
          class="hidden lg:flex"
          orientation="horizontal"
          :items="navigation_items"
        />
      </template>

      <template #body>
        <UNavigationMenu
          class="hidden md:flex"
          orientation="horizontal"
          :items="navigation_items"
        />
      </template>
    </UHeader>

    <UMain>
      <NuxtPage />
    </UMain>

    <UFooter>
      <template #left>
        <p class="text-sm text-muted">
          Built with Nuxt UI • © {{ new Date().getFullYear() }}
        </p>
      </template>

      <template #right>
        <UButton
          to="https://github.com/nuxt-ui-templates/starter"
          target="_blank"
          icon="i-simple-icons-github"
          aria-label="GitHub"
          color="neutral"
          variant="ghost"
        />
      </template>
    </UFooter>
  </UApp>
</template>
