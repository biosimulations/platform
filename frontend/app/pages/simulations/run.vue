<script setup lang="ts">
  //<editor-fold desc="Imports"
  import type {StepperItem} from "#ui/components/Stepper.vue";
  import type {RadioGroupItem} from "#ui/components/RadioGroup.vue";
  import type {BreadcrumbItem} from "#ui/components/Breadcrumb.vue";
  import {normalize_text} from "~/functions/functions";
  import {type ArchiveCompatibilityResponse, type ConglomerateStatus, RunSimulationPayload, type Simulator, type SimulatorSelection} from "~/models/simulators";
  import { z } from 'zod'
  //</editor-fold>

  const config = useRuntimeConfig()

  //<editor-fold desc="Breadcrumbs"
  const route = useRoute()
  const _lenis = useLenis()
  const routes = route.path.split('/').filter(i => i && i.trim().length > 0)
  const breadcrumbs = ref<BreadcrumbItem[]>([])

  onMounted(() => {
    breadcrumbs.value = [{label: 'Home', to: '/', icon: 'i-lucide-home'}]
    routes.forEach((route, _index) => {
      const breadcrumb = {
        label: normalize_text(route),
        to: `/${route}`
      }

      breadcrumbs.value.push(breadcrumb)
    })
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
      title: 'Almost Done',
      disabled: true,
      slot: 'end' as const,
    }
  ])
  const stepper = useTemplateRef('stepper')
  const stepper_position = ref<number>(0)
  //</editor-fold>

  const archive_file = ref<File | null>(null)
  const archive_url = ref<string | undefined>(undefined)

  const processing_archive = ref(false)

  const archive_processed = ref(false)
  const submission_payload = reactive(new RunSimulationPayload())

  const archive_compatibility_response = ref<ArchiveCompatibilityResponse | null>(null)
  const eligible_simulators = ref<Simulator[]>([])
  const simulator_version_options = ref<any[]>([])

  const commercial_disclosure_options = ref<RadioGroupItem[]>([
    {
      label: 'Commercial',
      value: true
    },
    {
      label: 'Research/Academic',
      value: false
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
    email_address: z.email().optional(),
    is_commercial: z.boolean().refine(value => value !== null, {message: 'Commercial acknowledgement is required'}),
    newsletter_consent: z.boolean().optional(),
  })

  const form_invalid = computed(() => {
    const fields_per_step = [[], ['_simulators'], ['name', 'email_address', 'is_commercial', 'newsletter_consent']]

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
          eligible_simulators.value = archive_compatibility_response.value.eligible_simulators?.slice()

          archive_processed.value = true
          advance()
        }
      } catch (error) {
        console.log(error)
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

      console.log(response)

      if (response) {
        await navigateTo(`/simulations/check-status/${response.processing_id}`)
      }
    } catch (error) {
      console.log(error)
    } finally {
      submitting.value = false
    }
  }
</script>

<template>
  <section class="w-full relative px-6 max-w-[1200px] mx-auto my-auto flex flex-col gap-4 items-center justify-center text-center md:text-left pt-5">
    <UBreadcrumb :items="breadcrumbs"></UBreadcrumb>

    <div class="page_header relative overflow-hidden w-full p-8 bg-primary-500 text-white flex flex-col items-center justify-center gap-2 rounded-lg">
      <div class="background zig-zag w-full h-full"></div>
      <h1 class="text-xl font-bold">Run a Simulation</h1>
      <p>Upload your archive, select preferred algorithms, and run your simulation.</p>
    </div>

    <UForm :schema="schema" :state="submission_payload" @submit="submit()">
      <UStepper v-model="stepper_position" class="w-full md:w-[700px] mt-7" ref="stepper" :items="steps">
        <template #archive>
          <UCard class="w-full" :ui="{body: 'bg-gray-50/50'}">
            <div class="w-full sm:w-max mx-auto flex flex-col items-center gap-4">
              <UFileUpload layout="list" accept=".omex" label="OMEX/Combine Archive" color="primary" description="Drop your OMEX/Combine archive here, or click to select" class="w-full sm:min-w-96 min-h-48" :class="{'opacity-50 pointer-events-none': archive_url, 'cursor-pointer': !archive_url}" v-model="archive_file" @change="handle_file_change()" />
              <USeparator :class="!!archive_file || archive_url ? 'opacity-50' : 'opacity-100'" label="or" />
              <div class="w-full flex flex-col gap-1" :class="{'opacity-50': !!archive_file}">
                <label for="omex_url"><small class="font-semibold">Enter URL for COMBINE/OMEX archive</small></label>
                <UInput name="omex_url" :disabled="!!archive_file" class="w-full sm:min-w-96" placeholder="https://*" v-model="archive_url" />
              </div>

              <UAlert
                v-if="archive_processed"
                class="w-full p-2 flex flex-col lg:flex-row items-center gap-[16px] justify-center lg:justify-between"
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
          <UCard class="w-full" :ui="{body: 'bg-gray-50/50'}">
            <h3 class="text-base font-bold">Select desired simulators and versions</h3>
            <div class="w-full mx-auto flex flex-col items-center gap-4 mt-2">
              <UFormField class="md:w-[250px]" label="Desired Simulators" name="simulators">
                <USelectMenu class="w-full" multiple name="simulators_selection" v-model="submission_payload._simulators" :items="eligible_simulators" placeholder="Select desired simulator(s)" label-key="name" />
              </UFormField>
<!--              <div class="w-full flex flex-col gap-1">
                <label for="simulators_selection"><small class="font-semibold">Desired Simulators</small></label>
                <USelectMenu data-lenis-prevent class="w-full" multiple name="simulators_selection" v-model="chosen_simulators" :items="eligible_simulators" placeholder="Select desired simulator(s)" label-key="name" @change="calculate_version_objects()" />
              </div>-->
              <USeparator v-if="submission_payload._simulators && submission_payload._simulators.length" />


              <UFormField class="md:w-[250px]" :label="simulator.name + ' Version'" :name="`simulators.${index}._selected_version`" v-for="(simulator, index) in submission_payload._simulators" :key="simulator.id">
                <USelectMenu class="w-full" :disabled="!submission_payload._simulators || !submission_payload._simulators.length" :items="simulator.versions" v-model="simulator._selected_version" placeholder="Select version" />
              </UFormField>
<!--              <div class="w-full flex flex-col gap-1" v-for="version_option of simulator_version_options">
                <label :for="version_option.id + '_version'"><small class="font-semibold">{{version_option.name}} Version</small></label>
                <USelectMenu data-lenis-prevent :disabled="!chosen_simulators || !chosen_simulators.length" :name="version_option.id + '_version'" :items="version_option.versions" v-model="version_option.version" placeholder="Select version"/>
              </div>-->
            </div>
          </UCard>
        </template>
        <template #end>
          <UCard class="w-full" :ui="{body: 'bg-gray-50/50'}">
            <h3 class="text-base font-bold">Just some final details</h3>
            <div class="w-full mx-auto md:max-w-[250px] flex flex-col items-center gap-4 mt-2">
              <UFormField class="w-full" label="Simulation Name" name="name">
                <UInput class="w-full" v-model="submission_payload.name" placeholder="Simulation Name" />
              </UFormField>
              <div class="w-full flex flex-col gap-1">
                <UFormField class="w-full" label="Email Address" hint="Optional" :ui="{label: 'text-sm'}" name="email_address">
                  <UInput class="w-full" type="email" v-model="submission_payload.email_address" placeholder="you@domain.com" />
                </UFormField>
                <UTooltip :delay-duration="0" text="Providing your email address links you to the simulation, which would otherwise be submitted completely anonymously.">
                  <small class="text-underline opacity-40 cursor-pointer hover:opacity-100"><i class="i-lucide-circle-question-mark"/> Why would I want to provide this?</small>
                </UTooltip>

                <UFormField class="w-full" name="newsletter_consent" hint="Optional">
                  <UCheckbox v-model="submission_payload.newsletter_consent" label="Subscribe to our newsletter" class="mt-2" />
                </UFormField>

              </div>
              <UFormField label="Is this simulation for commercial or research/academic purposes?" name="is_commercial">
                <URadioGroup v-model="submission_payload.is_commercial" required :items="commercial_disclosure_options"/>
              </UFormField>
              <!-- <div class="w-full flex flex-col gap-1">
                <label for="email_address"><small class="font-semibold">Is this simulation for commercial or research/academic purposes?<sup class="text-red-500" aria-label="Required field">*</sup></small></label>
                <URadioGroup v-model="submission_payload.is_commercial" required :items="commercial_disclosure_options"/>
              </div>-->
            </div>
          </UCard>
        </template>
    </UStepper>

    <div class="w-full md:max-w-full md:w-max md:min-w-[700px] flex gap-4 mt-4" :class="{'justify-between': stepper?.hasPrev, 'justify-end': !stepper?.hasPrev}">
      <UButton v-if="stepper?.hasPrev" type="button" class="cursor-pointer" variant="solid" leading-icon="i-lucide-arrow-left" @click="retreat()" label="Back"></UButton>
      <UButton type="button" class="cursor-pointer" v-if="!archive_processed && stepper_position == 0" leading-icon="i-fluent-sparkle-20-filled" :disabled="!archive_url && !archive_file" :variant="!archive_url && !archive_file ? 'outline' : 'solid'" color="primary" :loading="processing_archive" :label="!archive_url && !archive_file ? 'Process File/Validate URL' : (archive_url ? (processing_archive ? 'Validating URL' : 'Validate URL') : (processing_archive ? 'Processing File' : 'Process File'))" @click="process_archive()"></UButton>
      <UButton type="button" class="cursor-pointer" v-if="archive_processed && stepper_position >= 0 && stepper_position < steps.length - 1" trailing-icon="i-lucide-arrow-right" :disabled="!stepper?.hasNext || !archive_processed || form_invalid" @click="advance()" label="Next"></UButton>
      <UButton type="submit" class="cursor-pointer" v-if="archive_processed && stepper_position == steps.length - 1" leading-icon="i-lucide-save" :disabled="form_invalid || submitting || !archive_processed" :loading="submitting" label="Run Simulation"></UButton>
    </div>
    </UForm>
  </section>
</template>
