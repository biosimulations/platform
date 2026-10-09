<script setup lang="ts">
  //<editor-fold desc="Imports"
  import type {StepperItem} from "#ui/components/Stepper.vue";
  import type {RadioGroupItem} from "#ui/components/RadioGroup.vue";
  import type {BreadcrumbItem} from "#ui/components/Breadcrumb.vue";
  import {type ArchiveCompatibilityResponse, type ConglomerateStatus, RunSimulationPayload, type Simulator, type SimulatorSelection} from "~/models/simulators";
  import { z } from 'zod'
  import { randomName } from '@scaleway/random-name'
  import {useAuth0} from "@auth0/auth0-vue";
  //</editor-fold>

  const config = useRuntimeConfig()

  const route = useRoute()
  const _lenis = useLenis()
  const breadcrumbs: BreadcrumbItem[] = [
    { label: 'Home', to: '/', icon: 'i-lucide-home' },
    { label: 'Simulations', to: '/simulations' },
    { label: 'Run Simulation' }
  ]

  const { isAuthenticated, checkSession } = useAuth0()

  onMounted(() => {
    if (route.query.runName) {
      submission_payload.name = String(route.query.runName)
    }

    if (route.query.projectUrl) {
      is_rerun.value = true
      archive_url.value = String(route.query.projectUrl)
      process_archive()
    }

    if (isAuthenticated.value) {
      checkSession().catch((err) => {
        console.debug('Session sync on runs page skipped:', err)
      })
    }
  })
  //</editor-fold>

  //<editor-fold desc="Stepper"
  const steps = ref<StepperItem[]>([
    {
      title: 'OMEX/Combine Archive',
      disabled: true,
      slot: 'archive' as const,
    },
    {
      title: 'Choose Simulator(s)',
      disabled: true,
      slot: 'simulators' as const,
    },
    {
      title: 'Final Details',
      disabled: true,
      slot: 'end' as const,
    }
  ])
  const stepper = useTemplateRef('stepper')
  const stepper_position = ref<number>(0)
  //</editor-fold>

  const archive_file = ref<File | null>(null)
  const archive_url = ref<string | undefined>(undefined)
  const is_rerun = ref(false)

  const processing_archive = ref(false)
  const archive_processing_error = ref<string | null>(null)

  const archive_processed = ref(false)
  const submission_payload = reactive(new RunSimulationPayload())
  submission_payload.name = randomName()

  const archive_compatibility_response = ref<ArchiveCompatibilityResponse | null>(null)
  const eligible_simulators = ref<Simulator[]>([])
  const simulator_version_options = ref<any[]>([])
  const refreshing_auth = ref(false)

  const commercial_disclosure_options = ref<RadioGroupItem[]>([
    {
      label: 'Research/Academic',
      value: false
    },
    {
      label: 'Commercial',
      value: true
    }
  ])

  const submitting = ref(false)

  const schema = z.object({
    name: z.string('Simulation name is required'),
    _simulators: z.array(
      z.object({
        id: z.string(),
        name: z.string(),
        versions: z.array(z.string()),
        exact: z.boolean(),
        _selected_version: z.string('Select a version for this simulator').optional()
      })
    ).min(1, 'At least one simulator must be selected'),
    is_commercial: z.boolean().refine(value => value !== null, {message: 'Commercial acknowledgement is required'}),
  })

  const form_invalid = computed(() => {
    const fields_per_step = [[], ['_simulators'], ['name', 'is_commercial']]

    if (stepper_position.value !== undefined) {
      const current_fields: string[] = fields_per_step[stepper_position.value]!
      const step_schema = schema.pick(
        Object.fromEntries(current_fields.map(cf => [cf, true])) as any
      )

      return !step_schema.safeParse(submission_payload).success
    }

    return false
  })

  async function process_archive() {
    if (!archive_processed.value) {
      processing_archive.value = true

      const form_data = new FormData()
      if (archive_file.value) {
        const file_to_upload = Array.isArray(archive_file.value)
          ? archive_file.value[0]
          : archive_file.value

        form_data.append('uploaded_file', file_to_upload)
      }
      if (archive_url.value) {
        form_data.append('archive_url', archive_url.value)
      }

      try {
        const response: ArchiveCompatibilityResponse = await $fetch(`${config.public.api_url}/compatibility/check`, {
          method: 'POST',
          body: form_data
        })

        if (response) {
          submission_payload.omex_id = response.omex_id
          archive_compatibility_response.value = response as ArchiveCompatibilityResponse
          eligible_simulators.value = archive_compatibility_response.value.eligible_simulators?.slice() || []

          if (route.query.simulator) {
            const requestedSimulatorId = String(route.query.simulator).toLowerCase()
            const requestedVersion = route.query.simulatorVersion ? String(route.query.simulatorVersion) : undefined

            const matchedSimulator = eligible_simulators.value.find(s => s.id.toLowerCase() === requestedSimulatorId)

            if (matchedSimulator) {
              submission_payload._simulators = [matchedSimulator]

              if (requestedVersion && matchedSimulator.versions.includes(requestedVersion)) {
                matchedSimulator._selected_version = requestedVersion
              } else if (matchedSimulator.versions.length > 0) {
                matchedSimulator._selected_version = matchedSimulator.versions[0]
              }
            }
          }

          archive_processed.value = true
          advance()
        }
      } catch (error) {
        archive_processing_error.value = String(error)
      } finally {
        processing_archive.value = false
      }
    }
  }

  function handle_file_change() {
    archive_processed.value = false
  }

  function advance() {
    if (steps.value[stepper_position.value]!.slot == 'simulators') {
      submission_payload.simulators = simulator_version_options.value.map((sim_version_option: any) => {
        return {
          id: sim_version_option.id,
          version: sim_version_option.version
        }
      }) as SimulatorSelection[]
    }
    stepper_position.value += 1
  }

  function retreat() {
    stepper_position.value -= 1
  }

  function replacer(key: string, value: any) {
    if (key.startsWith('_')) {
      return undefined
    } else {
      return value
    }
  }

  async function submit() {
    submitting.value = true

    const simulator_selections = submission_payload._simulators.map((cs: Simulator) => {
      return {
        id: cs.id,
        version: cs.versions.find(cv => cv == cs._selected_version)
      }
    })
    submission_payload.simulators = simulator_selections as SimulatorSelection[]
    console.log(JSON.stringify(submission_payload, replacer))

    try {
      const response = await $fetch<ConglomerateStatus>(`${config.public.api_url}/simulations/run`, {
        method: 'POST',
        body: JSON.stringify(submission_payload, replacer)
      })

      if (response) {
        await navigateTo(`/simulations/check-status/${response.processing_id}`)
      }
    } catch (error) {
      console.log(error)
    } finally {
      submitting.value = false
    }
  }

  function preselect_version(simulators: Simulator[]) {
    simulators.forEach(s => !s._selected_version ? (s._selected_version = s.versions[0]) : null)
  }

  async function refreshAuth() {
    refreshing_auth.value = true
    await checkSession()
    refreshing_auth.value = false
  }
</script>

<template>
  <section class="w-full relative px-6 max-w-300 mx-auto my-auto flex flex-col gap-4 items-center justify-center text-center md:text-left pt-5">
    <UBreadcrumb class="mx-auto" :items="breadcrumbs" />

    <div class="page_header relative overflow-hidden w-full p-8 bg-primary-500 text-white flex flex-col items-center justify-center gap-2 rounded-lg">
      <div class="background zig-zag w-full h-full"></div>
      <h1 class="text-xl font-bold">Run a Simulation</h1>
      <p>Upload your archive, select preferred algorithms, and run your simulation in 3 simple steps.</p>
    </div>

    <UForm :schema="schema" :state="submission_payload" @submit="submit()">
      <UStepper v-model="stepper_position" class="w-full md:w-175 mt-7" ref="stepper" :items="steps">
        <template #archive>
          <UCard class="w-full">
            <template #header>
              <h3 class="text-base font-semibold">Provide Archive</h3>
            </template>
            <div class="w-full sm:w-max mx-auto flex flex-col items-center gap-4">
              <UFileUpload layout="list" accept=".omex" label="OMEX/Combine Archive" color="primary" description="Drop your OMEX/Combine archive here, or click to select" class="w-full sm:min-w-96 min-h-48" :class="{'opacity-50 pointer-events-none': archive_url || is_rerun, 'cursor-pointer': !archive_url && !is_rerun}" v-model="archive_file" @change="handle_file_change()" />
              <USeparator :class="!!archive_file || archive_url || is_rerun ? 'opacity-50' : 'opacity-100'" label="or" />
              <div class="w-full flex flex-col gap-1" :class="{'opacity-50': !!archive_file || is_rerun}">
                <label for="omex_url"><small class="font-semibold">Enter URL for COMBINE/OMEX archive</small></label>
                <UInput name="omex_url" :disabled="!!archive_file || is_rerun" class="w-full sm:min-w-96" placeholder="https://*" v-model="archive_url" />
              </div>

              <UAlert
                v-if="archive_processing_error"
                class="w-full p-2 flex flex-col lg:flex-row items-center gap-4 justify-center lg:justify-between"
                :title="archive_processing_error"
                icon="i-lucide-x"
                orientation="horizontal"
                variant="subtle"
                color="error"
                :ui="{
                  title: 'text-sm text-center md:text-left font-normal',
                  icon: 'size-5'
                }"
              >
              </UAlert>

              <UAlert
                v-if="archive_processed"
                class="w-full p-2 flex flex-col lg:flex-row items-center gap-4 justify-center lg:justify-between"
                title="Archive successfully processed. Proceed to next step."
                icon="i-lucide-check"
                orientation="horizontal"
                variant="subtle"
                color="primary"
                :ui="{
                  title: 'text-sm text-center md:text-left font-normal',
                  icon: 'size-5'
                }"
              >
              </UAlert>
            </div>
          </UCard>
        </template>
        <template #simulators>
          <UCard class="w-full">
            <template #header>
              <h3 class="text-base font-semibold">Select Desired Simulators & Versions</h3>
            </template>

            <div class="w-full mx-auto flex flex-col items-start gap-4">
              <UFormField class="md:min-w-62.5 w-full" label="Desired Simulators" name="simulators">
                <USelectMenu class="w-full" multiple name="simulators_selection" v-model="submission_payload._simulators" :items="eligible_simulators" @update:model-value="preselect_version($event)" placeholder="Select desired simulator(s)" label-key="name" />
              </UFormField>

              <div class="flex flex-col gap-4 pl-12 min-w-62.5 max-w-full w-max relative">
                <div class="absolute left-0 top-0 border-l h-[calc(100%-(--spacing(8)))] border-gray-300"></div>
                <div class="relative w-full" v-for="(simulator, index) in submission_payload._simulators" :key="simulator.id">
                  <div class="absolute -left-16 translate-x-1/2 -translate-y-1/2 top-1/2 aspect-square rounded-bl-2xl border-l border-b border-gray-300 w-8"></div>
                  <UFormField class="w-full" :label="simulator.name + ' Version'" :name="`simulators.${index}._selected_version`">
                    <USelectMenu class="w-full" :disabled="!submission_payload._simulators || !submission_payload._simulators.length" :items="simulator.versions" v-model="simulator._selected_version" placeholder="Select version" />
                  </UFormField>
                </div>
              </div>
<!--              <div class="w-full flex flex-col gap-1" v-for="version_option of simulator_version_options">
                <label :for="version_option.id + '_version'"><small class="font-semibold">{{version_option.name}} Version</small></label>
                <USelectMenu data-lenis-prevent :disabled="!chosen_simulators || !chosen_simulators.length" :name="version_option.id + '_version'" :items="version_option.versions" v-model="version_option.version" placeholder="Select version"/>
              </div>-->
            </div>
          </UCard>
        </template>
        <template #end>
          <UCard class="w-full">
            <template #header>
              <h3 class="text-base font-semibold">Final Details</h3>
            </template>

            <div class="w-full flex items-center gap-4">
              <UFormField class="flex-1" label="Simulation Name" name="name">
                <UInput class="w-full" v-model="submission_payload.name" placeholder="Simulation Name" :ui="{trailing: 'pr-0.5'}">
                  <template #trailing>
                    <UButton color="neutral" variant="ghost" icon="i-lucide-refresh-cw" size="sm" @click="submission_payload.name = randomName()"/>
                  </template>
                </UInput>
              </UFormField>
              <UFormField class="flex-1" label="Simulation Purpose" name="is_commercial">
                <USelect class="w-full" v-model="submission_payload.is_commercial" required :items="commercial_disclosure_options"/>
              </UFormField>
            </div>

            <div >
              <UAlert class="w-full mt-4" v-if="!isAuthenticated" title="Sign in to own this run" icon="i-lucide-user" color="info" variant="subtle">
                <template #description>
                  <span>You are not currently signed in. If you want to own this run rather than submit it anonymously, sign in now. Don't have an account yet? Create an account today!</span>

                  <div class="w-full flex items-center justify-between gap-3 mt-4">
                    <UButton color="info" variant="subtle" loading-icon="i-lucide-refresh-cw" :loading="refreshing_auth" leading-icon="i-lucide-refresh-cw" @click="refreshAuth">Already Signed In? Refresh now!</UButton>

                    <div class="flex items-center justify-end gap-3 mt-4">
                      <UButton color="info" variant="subtle" target="_blank" to="/register">Register</UButton>
                      <UButton color="info" target="_blank" to="/login">Sign In</UButton>
                    </div>
                  </div>
                </template>
              </UAlert>
            </div>
          </UCard>
        </template>
    </UStepper>

    <div class="w-full md:max-w-full md:w-max md:min-w-175 flex gap-4 mt-4" :class="{'justify-between': stepper?.hasPrev, 'justify-end': !stepper?.hasPrev}">
      <UButton v-if="stepper?.hasPrev" type="button" class="cursor-pointer" variant="link" leading-icon="i-lucide-arrow-left" @click="retreat()" label="Back"></UButton>
      <UButton type="button" class="cursor-pointer" v-if="!archive_processed && stepper_position == 0" leading-icon="i-fluent-sparkle-20-filled" :disabled="!archive_url && !archive_file" :variant="!archive_url && !archive_file ? 'outline' : 'solid'" color="primary" :loading="processing_archive" :label="!archive_url && !archive_file ? 'Process File/Validate URL' : (archive_url ? (processing_archive ? 'Validating URL' : 'Validate URL') : (processing_archive ? 'Processing File' : 'Process File'))" @click="process_archive()"></UButton>
      <UButton type="button" class="cursor-pointer" v-if="archive_processed && stepper_position >= 0 && stepper_position < steps.length - 1" trailing-icon="i-lucide-arrow-right" :disabled="!stepper?.hasNext || !archive_processed || form_invalid" @click="advance()" label="Next"></UButton>
      <UButton type="submit" class="cursor-pointer" v-if="archive_processed && stepper_position == steps.length - 1" leading-icon="i-lucide-save" :disabled="form_invalid || submitting || !archive_processed" :loading="submitting" label="Run Simulation"></UButton>
    </div>
    </UForm>
  </section>
</template>
