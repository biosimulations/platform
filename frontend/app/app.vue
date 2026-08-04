<script setup lang="ts">
import type { NavigationMenuItem } from '@nuxt/ui'

const title = 'BioSimulations'
const description = 'Web application for sharing dynamical models of biological systems and visualizing their results'
const keywords = 'biomodel, biosimulation, mathematical model, numerical simulation, computational biology, systems biology, reproduce, reuse, standards, CellML, COMBINE, Docker, OMEX, SBML, SED-ML, Smoldyn, Vega'

useSeoMeta({
  title,
  description,
  keywords,
  author: 'Center for Reproducible Biomedical Modeling, info@biosimulations.org',
  ogTitle: title,
  ogDescription: description,
  ogImage: 'https://static.biosimulations.org/biosimulations-logo/logo-github.png',
  twitterImage: 'https://static.biosimulations.org/biosimulations-logo/logo-github.png',
  twitterCard: 'summary_large_image',
  applicationName: 'BioSimulations',
  themeColor: '#2196f3',
})

useHead({
  meta: [
    { name: 'viewport', content: 'width=device-width, initial-scale=1' },
    { name: 'language', content: 'EN' },
    { name: 'copyright', content: 'Center for Reproducible Biomedical Modeling' },
    { name: 'reply-to', content: 'info@biosimulations.org' },
    { name: 'url', content: 'https://www.biosimulations.org' },
    { name: 'identifier-URL', content: 'https://www.biosimulations.org' }
  ],
  link: [
    { rel: 'icon', href: '/images/favicon.svg' }
  ],
  htmlAttrs: {
    lang: 'en'
  },
  script: [
    {
      type: 'application/ld+json',
      innerHTML: JSON.stringify({
        "@context": "https://schema.org",
        "@type": "WebApplication",
        "name": "BioSimulations",
        "applicationSuite": "BioSimulations",
        "applicationCategory": "Science",
        "applicationSubCategory": "Simulation",
        "license": ["https://docs.biosimulations.org/about/license/", "https://identifiers.org/spdx:MIT"],
        "softwareHelp": "https://docs.biosimulations.org",
        "discussionUrl": "https://github.com/biosimulations/biosimulations/issues",
        "releaseNotes": "https://docs.biosimulations.org/news/changelog/",
        "abstract": description,
        "keywords": keywords.split(', '),
        "image": "https://static.biosimulations.org/biosimulations-logo/logo-github.png",
        "educationalLevel": "advanced",
        "creator": {
          "@type": "Organization",
          "name": "BioSimulations",
          "email": "info@biosimulations.org",
          "logo": "https://static.biosimulations.org/biosimulations-logo/logo-github.png"
        },
        "url": "https://biosimulations.org",
        "identifier": [
          "http://identifiers.org/biocatalogue.service:4043",
          "http://identifiers.org/biotools:biosimulations",
          "http://identifiers.org/fairsharing:bsg-d001721",
          "https://registry.identifiers.org/registry/biosimulations",
          "http://identifiers.org/re3data:r3d100013361",
          "http://identifiers.org/RRID:SCR_018733"
        ],
        "offers": {
          "@type": "Offer",
          "price": 0,
          "priceCurrency": "USD"
        }
      })
    },
    {
      type: 'application/ld+json',
      innerHTML: JSON.stringify({
        "@context": "https://schema.org",
        "@type": ["Dataset", "DataCatalog"],
        "name": "BioSimulations",
        "description": "Database of published biosimulation projects, including models, simulation experiments, simulation results, and data visualizations of simulation results.",
        "distribution": {
          "@type": "DataDownload",
          "encodingFormat": "application/json",
          "contentUrl": "https://api.biosimulations.org/projects"
        },
        "discussionUrl": "https://github.com/biosimulations/biosimulations/issues",
        "educationalLevel": "advanced",
        "license": ["https://docs.biosimulations.org/about/license/", "https://identifiers.org/spdx:MIT"],
        "keywords": keywords.split(', '),
        "image": "https://static.biosimulations.org/biosimulations-logo/logo-github.png",
        "creator": {
          "@type": "Organization",
          "name": "BioSimulations",
          "email": "info@biosimulations.org",
          "logo": "https://static.biosimulations.org/biosimulations-logo/logo-github.png"
        },
        "url": "https://biosimulations.org",
        "identifier": [
          "http://identifiers.org/biocatalogue.service:4043",
          "http://identifiers.org/biotools:biosimulations",
          "http://identifiers.org/fairsharing:bsg-d001721",
          "https://registry.identifiers.org/registry/biosimulations",
          "http://identifiers.org/re3data:r3d100013361",
          "http://identifiers.org/RRID:SCR_018733"
        ],
        "offers": {
          "@type": "Offer",
          "price": 0,
          "priceCurrency": "USD"
        }
      })
    },
    {
      type: 'application/ld+json',
      innerHTML: JSON.stringify({
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "BioSimulations",
        "email": "info@biosimulations.org",
        "url": "https://biosimulations.org",
        "logo": "https://static.biosimulations.org/biosimulations-logo/logo-github.png"
      })
    }
  ]
})

const navigation_items = ref<NavigationMenuItem[][]>([
  [
    {
      label: 'BioSim DB',
      icon: 'i-lucide-database',
      to: '/biosim-db',
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
          class: 'cursor-pointer',
        },
        {
          label: 'Browse Simulations',
          // description: 'Browse over 100,000 published simulations.',
          icon: 'i-lucide-search',
          to: '/simulations',
          class: 'cursor-pointer',
        },
      ]
    },
    {
      label: 'Simulators',
      class: 'cursor-pointer',
      children: [
        {
          label: 'Get Started',
          icon: 'i-lucide-monitor-cog',
          to: '/simulators/get-started',
        },
        {
          label: 'Browse Simulators',
          icon: 'i-lucide-search',
          to: '/simulators',
        },
        {
          label: 'Suggest a simulator',
          icon: 'i-lucide-lightbulb',
          to: '/simulators/suggest',
          class: 'cursor-pointer',
        },
        {
          label: 'Validate a simulator',
          icon: 'i-lucide-cpu',
          to: '/simulators/validate',
          class: 'cursor-pointer',
        },
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
          class: 'cursor-pointer',
        },
        {
          label: 'Validate a model',
          icon: 'i-lucide-file-check',
          to: '/utilities/validate-model',
          class: 'cursor-pointer',
        },
        {
          label: 'Validate a simulation',
          icon: 'i-lucide-activity',
          to: '/utilities/validate-simulation',
          class: 'cursor-pointer',
        },
        {
          label: 'Validate metadata',
          icon: 'i-lucide-tags',
          to: '/utilities/validate-metadata',
          class: 'cursor-pointer',
        },
        {
          label: 'Validate a project',
          icon: 'i-lucide-folder-check',
          to: '/utilities/validate-project',
          class: 'cursor-pointer',
        },
        {
          label: 'Describe visualizations',
          icon: 'i-lucide-bar-chart-3',
          to: 'utilities/describe-visualizations',
          class: 'cursor-pointer',
        },
        {
          label: 'BioSimulations REST API',
          icon: 'i-lucide-server',
          to: 'https://api.biosimulations.org',
          class: 'cursor-pointer',
        },
        {
          label: 'BioSimulators REST API',
          icon: 'i-lucide-database',
          to: 'https://api.biosimulators.org',
          class: 'cursor-pointer',
        },
        {
          label: 'Verification REST API (new)',
          icon: 'i-lucide-shield-check',
          to: 'https://biochecknet.biosimulations.org/docs',
          class: 'cursor-pointer',
        },
      ]
    },
    {
      label: 'Learn',
      // icon: 'i-lucide-book-open',
      to: '',
      class: 'cursor-pointer',
    },
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
    // console.log(lenis)
  })
})

</script>

<template>
  <VueLenis root :options="lenisOptions" />
  <UApp>

    <UHeader class="w-max-[1200px] mx-auto" mode="drawer">
      <template #title>
        <NuxtLink to="/">
          <img class="w-auto h-8 shrink-0" src="/images/full_logo_color.svg" alt="BioSimulations Logo - Bio and Simulations are bisected by a circular icon of two arrows interwoven like a DNA molecule">
        </NuxtLink>
      </template>

<!--      <UNavigationMenu class="hidden md:flex" orientation="horizontal" :items="navigation_items"></UNavigationMenu>-->
       <template #right>
          <div class="flex items-center gap-3">
            <UNavigationMenu
              class="hidden lg:flex"
              orientation="horizontal"
              :items="navigation_items"
            />
            <ClientOnly>
              <AuthMenu />
            </ClientOnly>
          </div>
        </template>

      <template #body>
        <UNavigationMenu class="hidden md:flex" orientation="horizontal" :items="navigation_items"></UNavigationMenu>
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
