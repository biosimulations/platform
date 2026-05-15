<script setup lang="ts">
import type { PageFeatureProps } from '#ui/components/PageFeature.vue'
import type { BreadcrumbItem } from '#ui/components/Breadcrumb.vue'
import { normalize_text } from '~/functions/functions'

const features = ref<PageFeatureProps[]>([
  {
    title: 'Find Simulation Tools',
    description: 'Find tools that can run specific simulations and download and run them.',
    icon: 'i-lucide-refresh-cw'
  },
  {
    title: 'Run Simulations',
    description: 'Run a broad range of modeling frameworks, simulation algorithms, and model formats.',
    icon: 'i-lucide-settings'
  },
  {
    title: 'Compare Simulation Tools',
    description: 'Compare simulation tools to identify the strengths of different approaches, find bugs, and drive standardization.',
    icon: 'i-lucide-database'
  }
])

const route = useRoute()
const routes = route.path.split('/').filter(i => i && i.trim().length > 0)
const breadcrumbs = ref<BreadcrumbItem[]>([])

routes.forEach((route) => {
  const breadcrumb = {
    label: normalize_text(route),
    to: `/${route}`
  }

  breadcrumbs.value.push(breadcrumb)
})

const tabs = [
  {
    label: 'CLI with Python',
    icon: 'i-lucide-python',
    slot: 'python-cli'
  },
  {
    label: 'CLI with Docker',
    icon: 'i-lucide-python',
    slot: 'docker-cli'
  },
  {
    label: 'Programmatically with Python',
    icon: 'i-lucide-python',
    slot: 'python-programmatically'
  },
  {
    label: 'In-Browser with Binder',
    icon: 'i-lucide-python',
    slot: 'binder'
  }
]
</script>

<template>
  <div class="w-full max-w-[1200px] px-6 mx-auto">
    <div class="flex items-center justify-center px-4 py-2 bg-gray-50 rounded-lg mt-3">
      <UBreadcrumb :items="breadcrumbs" />
    </div>
    <UPageSection
      headline="Get Started"
      description="Use the instructions below to set up your system for running your favorite simulators"
      :features="features"
    >
      <template #title>
        <h2 class="text-4xl font-bold">
          Quick setup, quicker research - it's only (bio) logical.
        </h2>
      </template>
    </UPageSection>

    <USeparator
      label="Select Installation Method"
      :ui="{
        label: 'text-base font-bold'
      }"
    />
    <!--    <h3 class="w-full text-xl font-bold text-center">Select Installation Method</h3> -->
    <UTabs
      class="mt-4"
      :items="tabs"
    >
      <template #python-cli>
        <UCard>
          <template #header>
            <h3 class="text-xl font-bold">
              Run a simulator using Python from the CLI:
            </h3>
            <p class="text-base font-lighter mt-2">
              <ULink class="underline cursor-pointer hover:text-black">Explore the documentation</ULink> for a comprehensive guide and best practices.
            </p>
          </template>

          <ol class="list-decimal list-inside">
            <li>
              <span>Install the BioSimulator you want with pip. Here we will use <UBadge
                color="neutral"
                variant="outline"
              >biosimulators-tellurium</UBadge>. A full list of BioSimulators packages can be found <ULink to="/simulators">here</ULink>.</span>
              <div class="w-full my-3 p-4 font-mono rounded-lg flex items-start justify-start gap-3 bg-black text-white">
                <p class="text-green-500">
                  %
                </p>
                <p>pip install biosimulators-tellurium</p>
              </div>
            </li>
            <li>
              <span>Run the following from the command line, passing an OMEX filepath as -i and the output dir as -o</span>
              <div class="w-full my-3 p-4 font-mono rounded-lg flex items-start justify-start gap-3 bg-black text-white">
                <p class="text-green-500">
                  %
                </p>
                <p>biosimulators-tellurium \<br>-i ~/experiments/my-file.omex \<br>-o ~/Desktop/results</p>
              </div>
            </li>
            <li>
              <span>Go to the directory and view your results.</span>
              <div class="w-full my-3 p-4 font-mono rounded-lg flex items-start justify-start gap-3 bg-black text-white">
                <p class="text-green-500">
                  %
                </p>
                <p>cd ~/Desktop/results</p>
              </div>
            </li>
          </ol>
        </UCard>
      </template>
      <template #docker-cli>
        <UCard>
          <template #header>
            <h3 class="text-xl font-bold">
              Run a simulator with Docker
            </h3>
            <p class="text-base font-lighter mt-2">
              <ULink class="underline cursor-pointer hover:text-black">Explore the documentation</ULink> for a comprehensive guide and best practices.
            </p>
          </template>
          <span>Here we will use <UBadge
            color="neutral"
            variant="outline"
          >biosimulators-tellurium</UBadge> as an example:</span>
          <div class="w-full my-3 p-4 font-mono rounded-lg flex items-start justify-start gap-3 bg-black text-white">
            <p class="text-green-500">
              %
            </p>
            <p>docker pull \<br>ghcr.io/biosimulators/tellurium:2.2.8</p>
          </div>
          <div class="w-full my-3 p-4 font-mono rounded-lg flex items-start justify-start gap-3 bg-black text-white">
            <p class="text-green-500">
              %
            </p>
            <p>docker run \<br>ghcr.io/biosimulators/tellurium:2.2.8 \<br>-i /path/to/archive.omex \<br>-o /path/to/outputs</p>
          </div>
        </UCard>
      </template>
      <template #python-programmatically>
        <UCard>
          <template #header>
            <h3 class="text-xl font-bold">
              Run a simulator programmatically with Python:
            </h3>
            <p class="text-base font-lighter mt-2">
              <ULink class="underline cursor-pointer hover:text-black">Explore the documentation</ULink> for a comprehensive guide and best practices.
            </p>
          </template>

          <ol class="list-decimal list-inside">
            <li>
              <span> Import the desired simulator. For example: <UBadge
                color="neutral"
                variant="outline"
              >biosimulators_tellurium</UBadge></span>
              <div class="w-full my-3 p-4 font-mono rounded-lg flex items-start justify-start gap-3 bg-black text-white">
                <p>import biosimulators_tellurium as bt</p>
              </div>
            </li>
            <li>
              <span>Define the paths to both your OMEX file and desired output destination:</span>
              <div class="w-full my-3 p-4 font-mono rounded-lg flex items-start justify-start gap-3 bg-black text-white">
                <p>omex = 'Ciliberto-J-Cell-Biol-2003-morphogenesis-checkpoint-continuous.omex'</p>
              </div>
              <div class="w-full my-3 p-4 font-mono rounded-lg flex items-start justify-start gap-3 bg-black text-white">
                <p>results_dir = '/content/my_results'</p>
              </div>
            </li>
            <li>
              <span>Run the simulation and get the results and log. Print the results:</span>
              <div class="w-full my-3 p-4 font-mono rounded-lg flex items-start justify-start gap-3 bg-black text-white">
                <p>
                  results, log = bt.exec_sedml_docs_in_combine_archive(<br>
                  &emsp;omex,<br>
                  &emsp;results_dir,<br>
                  &emsp;config<br>
                  )<br><br>

                  print(results)<br>
                  print(log)
                </p>
              </div>
            </li>
          </ol>

          <span>Here is the full implementation:</span>
          <div class="w-full my-3 p-4 font-mono rounded-lg flex items-start justify-start gap-3 bg-black text-white">
            <p>
              import biosimulators_tellurium as bt<br><br>
              omex = 'Ciliberto-J-Cell-Biol-2003-morphogenesis-checkpoint-continuous.omex'<br>
              results_dir = '/content/my_results'<br>
              results, log = bt.exec_sedml_docs_in_combine_archive(<br>
              &emsp;omex,<br>
              &emsp;results_dir,<br>
              &emsp;config<br>
              )<br><br>

              print(results)<br>
              print(log)
            </p>
          </div>
        </UCard>
      </template>
      <template #binder>
        <UCard>
          <h3 class="text-xl font-bold">
            A fully interactive Binder tutorial is available <ULink
              to="https://mybinder.org/v2/gh/biosimulators/Biosimulators_tutorials/HEAD"
              target="_blank"
              class="underline cursor-pointer hover:text-black"
            >here.</ULink>
          </h3>
        </UCard>
      </template>
    </UTabs>
  </div>
</template>
