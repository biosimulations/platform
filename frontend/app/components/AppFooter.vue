<script setup lang="ts">
interface FooterLink {
  label: string
  to: string
  icon?: string
  external?: boolean
  badge?: string
}

interface FooterColumnCategory {
  title: string
  links: FooterLink[]
}

const currentYear = new Date().getFullYear()

// Arbitrated categories combining the Angular platform specifications,
// core application routes, and developer API resources.
const categories: FooterColumnCategory[] = [
  {
    title: 'Platform & Simulations',
    links: [
      {
        label: 'BioSim DB',
        to: '/biosim-db',
        icon: 'i-lucide-database',
        badge: 'Explore'
      },
      {
        label: 'Run a Simulation',
        to: '/simulations/run',
        icon: 'i-fluent-sparkle-20-filled'
      },
      {
        label: 'Browse Simulations',
        to: '/simulations',
        icon: 'i-lucide-search'
      },
      {
        label: 'Browse Simulators',
        to: '/simulators',
        icon: 'i-lucide-cpu'
      },
      {
        label: 'Suggest a Simulator',
        to: '/simulators/suggest',
        icon: 'i-lucide-lightbulb'
      },
      {
        label: 'Validate a Simulator',
        to: '/simulators/validate',
        icon: 'i-lucide-check-circle'
      }
    ]
  },

  {
    title: 'Developers & APIs',
    links: [
      {
        label: 'BioSimulations REST API',
        to: 'https://api.biosimulations.org',
        icon: 'i-lucide-server',
        external: true
      },
      {
        label: 'BioSimulators REST API',
        to: 'https://api.biosimulators.org',
        icon: 'i-lucide-network',
        external: true
      },
      {
        label: 'Verification REST API',
        to: 'https://biochecknet.biosimulations.org/docs',
        icon: 'i-lucide-shield-check',
        external: true,
        badge: 'New'
      },
      {
        label: 'Validate a Model',
        to: '/utilities/validate-model',
        icon: 'i-lucide-file-check'
      },
      {
        label: 'Describe Visualizations',
        to: '/utilities/describe-visualizations',
        icon: 'i-lucide-pie-chart'
      },
      {
        label: 'GitHub Code Repository',
        to: 'https://github.com/biosimulations/biosimulations',
        icon: 'i-simple-icons-github',
        external: true
      }
    ]
  },
  {
    title: 'Community & About',
    links: [
      {
        label: 'Documentation Hub',
        to: 'https://docs.biosimulations.org',
        icon: 'i-lucide-book-open',
        external: true
      },
      {
        label: 'Frequently Asked Questions',
        to: 'https://docs.biosimulations.org/users/faqs/',
        icon: 'i-lucide-help-circle',
        external: true
      },
      {
        label: 'Contact Support',
        to: 'https://docs.biosimulations.org/about/contact/',
        icon: 'i-lucide-mail',
        external: true
      },
      {
        label: 'About & Development Team',
        to: 'https://docs.biosimulations.org/about/',
        icon: 'i-lucide-users',
        external: true
      },
      {
        label: 'Terms of Service',
        to: 'https://docs.biosimulations.org/about/terms/',
        icon: 'i-lucide-file-text',
        external: true
      },
      {
        label: 'Privacy Policy',
        to: 'https://docs.biosimulations.org/about/privacy',
        icon: 'i-lucide-shield',
        external: true
      },
      {
        label: 'Vivarium Collective',
        to: 'https://vivarium-collective.github.io/',
        icon: 'i-lucide-external-link',
        external: true
      }
    ]
  }
]
</script>

<template>
  <footer class="border-t border-slate-200/80 dark:border-slate-800/80 bg-slate-50/75 dark:bg-slate-950/60 backdrop-blur-sm">
    <UContainer class="py-12 sm:py-16">
      <!-- Call to Action Banner -->
      <div
        class="relative overflow-hidden rounded-2xl border border-blue-200/60 dark:border-blue-900/40 bg-gradient-to-r from-blue-500/10 via-cyan-500/5 to-amber-500/10 dark:from-blue-950/40 dark:via-slate-900/30 dark:to-amber-950/30 p-6 sm:p-8 lg:p-10 mb-12 shadow-xs"
      >
        <div class="relative z-10 flex flex-col lg:flex-row lg:items-center lg:justify-between gap-6">
          <div class="space-y-2 max-w-2xl">
            <div class="inline-flex items-center gap-2">
              <UBadge color="primary" variant="subtle" size="sm" class="rounded-full px-2.5">
                <UIcon name="i-fluent-sparkle-20-filled" class="size-3.5 mr-1" />
                Reproducible Biomedical Modeling
              </UBadge>
            </div>
            <h2 class="text-xl sm:text-2xl font-bold tracking-tight text-slate-900 dark:text-white">
              Ready to run or publish biological simulations?
            </h2>
            <p class="text-sm sm:text-base text-slate-600 dark:text-slate-300 leading-relaxed">
              Execute standardized models in the cloud with community-validated tools, or search thousands of published simulation projects in BioSim DB.
            </p>
          </div>

          <div class="flex flex-wrap items-center gap-3 shrink-0">
            <UButton
              to="/simulations/run"
              color="primary"
              size="lg"
              icon="i-fluent-sparkle-20-filled"
              label="Run a Simulation"
              class="font-semibold shadow-sm"
            />
            <UButton
              to="/biosim-db"
              color="neutral"
              variant="outline"
              size="lg"
              icon="i-lucide-database"
              label="Browse BioSim DB"
              class="font-medium bg-white/60 dark:bg-slate-900/60"
            />
            <UButton
              to="https://docs.biosimulations.org"
              target="_blank"
              color="neutral"
              variant="ghost"
              size="lg"
              icon="i-lucide-book-open"
              label="Docs"
              trailing-icon="i-lucide-external-link"
              class="font-medium"
            />
          </div>
        </div>
      </div>

      <!-- Main Footer Content: Brand Column + Navigation Links Grid -->
      <div class="grid grid-cols-1 xl:grid-cols-12 gap-10 xl:gap-12 pb-10">
        <!-- Brand & Mission Column -->
        <div class="xl:col-span-4 space-y-4">
          <NuxtLink to="/" class="inline-block focus:outline-none" aria-label="BioSimulations Home">
            <img
              class="w-auto h-9 shrink-0"
              src="/images/full_logo_color.svg"
              alt="BioSimulations Logo - Bio and Simulations bisected by double helix arrows"
            />
          </NuxtLink>

          <p class="text-sm text-slate-600 dark:text-slate-400 leading-relaxed">
            BioSimulations is an open platform for discovering, executing, and standardizing biological simulations and computational models across diverse biological frameworks.
          </p>

          <!-- System Status Badge -->
          <div class="pt-1">
            <NuxtLink
              to="https://status.biosimulations.org"
              target="_blank"
              class="inline-flex items-center gap-2 text-xs font-medium px-3 py-1.5 rounded-full border border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-500/20 transition-colors"
            >
              <span class="relative flex size-2">
                <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                <span class="relative inline-flex rounded-full size-2 bg-emerald-500" />
              </span>
              <span>Systems Operational</span>
              <UIcon name="i-lucide-external-link" class="size-3 opacity-60 ml-0.5" />
            </NuxtLink>
          </div>

          <!-- Academic Affiliation -->
          <div class="pt-3 border-t border-slate-200/60 dark:border-slate-800/60 text-xs text-slate-500 dark:text-slate-400 space-y-1.5">
            <p class="font-medium text-slate-700 dark:text-slate-300">
              Center for Reproducible Biomedical Modeling
            </p>
            <p>
              Developed collaboratively by the Karr Lab (Icahn School of Medicine at Mount Sinai), CCAM (UConn Health), and the University of Washington.
            </p>
            <p class="text-[11px] text-slate-400 dark:text-slate-500">
              Supported by the National Institutes of Health (NIH NIBIB &amp; NIGMS) and the National Science Foundation (NSF).
            </p>
          </div>
        </div>

        <!-- 3 Links Columns -->
        <div class="xl:col-span-8 grid grid-cols-1 sm:grid-cols-3 gap-8 sm:gap-6">
          <div v-for="category in categories" :key="category.title" class="space-y-3">
            <h3 class="text-xs font-semibold uppercase tracking-wider text-slate-900 dark:text-slate-200">
              {{ category.title }}
            </h3>

            <ul class="space-y-2 text-sm">
              <li v-for="link in category.links" :key="link.label">
                <NuxtLink
                  :to="link.to"
                  :target="link.external ? '_blank' : undefined"
                  :rel="link.external ? 'noopener noreferrer' : undefined"
                  class="group flex items-center justify-between text-slate-600 hover:text-blue-600 dark:text-slate-400 dark:hover:text-blue-400 transition-colors py-0.5"
                >
                  <span class="flex items-center gap-2 truncate">
                    <UIcon
                      v-if="link.icon"
                      :name="link.icon"
                      class="size-4 shrink-0 text-slate-400 group-hover:text-blue-500 dark:text-slate-500 dark:group-hover:text-blue-400 transition-colors"
                    />
                    <span class="truncate">{{ link.label }}</span>
                  </span>

                  <span class="flex items-center gap-1 shrink-0 ml-1.5">
                    <UBadge
                      v-if="link.badge"
                      size="xs"
                      variant="subtle"
                      color="primary"
                      class="text-[10px] px-1 py-0 h-4 leading-none"
                    >
                      {{ link.badge }}
                    </UBadge>
                    <UIcon
                      v-if="link.external"
                      name="i-lucide-arrow-up-right"
                      class="size-3 text-slate-400 dark:text-slate-500 opacity-0 group-hover:opacity-100 transition-opacity"
                    />
                  </span>
                </NuxtLink>
              </li>
            </ul>
          </div>
        </div>
      </div>

      <!-- Bottom Bar: Legal & Attribution & External Links -->
      <div class="pt-8 border-t border-slate-200/80 dark:border-slate-800/80 flex flex-col md:flex-row items-center justify-between gap-4 text-xs text-slate-500 dark:text-slate-400">
        <div class="flex flex-wrap items-center justify-center md:justify-start gap-x-3 gap-y-1 text-center md:text-left">
          <span>&copy; {{ currentYear }} BioSimulations.</span>
          <span class="hidden sm:inline">&bull;</span>
          <span>Released under the <a href="https://github.com/biosimulations/biosimulations/blob/dev/LICENSE" target="_blank" rel="noopener noreferrer" class="hover:underline hover:text-slate-700 dark:hover:text-slate-300">MIT Open Source License</a>.</span>
        </div>

        <div class="flex items-center gap-1.5">
          <UButton
            to="https://github.com/biosimulations/biosimulations"
            target="_blank"
            icon="i-simple-icons-github"
            aria-label="BioSimulations on GitHub"
            color="neutral"
            variant="ghost"
            size="sm"
          />
          <UButton
            to="https://docs.biosimulations.org"
            target="_blank"
            icon="i-lucide-book-open"
            aria-label="Documentation"
            color="neutral"
            variant="ghost"
            size="sm"
          />
          <UButton
            to="https://status.biosimulations.org"
            target="_blank"
            icon="i-lucide-activity"
            aria-label="System Status"
            color="neutral"
            variant="ghost"
            size="sm"
          />
          <UButton
            to="mailto:info@biosimulations.org"
            icon="i-lucide-mail"
            aria-label="Contact Support by Email"
            color="neutral"
            variant="ghost"
            size="sm"
          />
        </div>
      </div>
    </UContainer>
  </footer>
</template>
