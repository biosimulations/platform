<script setup lang="ts">
  import type {ConglomerateStatus} from "~/models/simulators";
  import {normalize_text} from "~/functions/functions";
  import type {BreadcrumbItem} from "#ui/components/Breadcrumb.vue";
  import { DotLottieVue } from '@lottiefiles/dotlottie-vue'

  const route = useRoute()
  const routes = route.path.split('/').filter(i => i && i.trim().length > 0)
  const breadcrumbs = ref<BreadcrumbItem[]>([])
  const processing_status = ref<ConglomerateStatus | null>(null)
  const seconds_remaining = ref<number>(0)
  const interval = 5
  const processing_id = routes[routes.length - 1]

  const finished = ref(false)

  let countdown_interval: any = undefined
  async function check_status() {
    seconds_remaining.value = 0

    if (countdown_interval) {
      clearInterval(countdown_interval)
    }

    try {
      const response = await $fetch('https://biosim.biosimulations.org/simulations/' + processing_id, {
        method: 'GET'
      })

      if (response) {
        processing_status.value = response as ConglomerateStatus

        if ((response as ConglomerateStatus).jobs.filter(j => j.status !== 'processing').length == (response as ConglomerateStatus).jobs.length) {
          finished.value = true
        } else {
          reset_interval()
        }
      }
    } catch (error: any) {
      const message = useState('message')
      message.value = error.data.detail

      await navigateTo('/error')
    }
  }

  function reset_interval() {
    countdown_interval = setInterval(() => {
      if (seconds_remaining.value > 0) {
        seconds_remaining.value -= 1
      } else {
        check_status()
      }
    }, 1000)

    seconds_remaining.value = interval
  }

  onMounted(() => {
    routes.forEach((route, index) => {
      const breadcrumb = {
        label: normalize_text(route),
        to: `/${route}`
      }

      breadcrumbs.value.push(breadcrumb)
    })

    check_status()
  })
</script>

<template>
  <section class="w-full min-h-[calc(100vh-var(--ui-header-height))] px-6 max-w-[1200px] mx-auto flex flex-col gap-4 items-center justify-center text-center md:text-left">
    <!--<UBreadcrumb :items="breadcrumbs"></UBreadcrumb>-->

    <div v-if="processing_status == null" class="flex items-center gap-2">
      <Icon name="i-svg-spinners:ring-resize" size="20" />
      <span class="text-base"><em>Fetching details...</em></span>
    </div>

    <div class="w-full md:w-max md:max-w-[700px] lg:max-w-[900px] flex flex-col items-center justify-center gap-2" v-if="processing_status !== null">
      <DotLottieVue class="w-[200px] aspect-square" autoplay src="/animations/success.lottie" />
      <h1 class="text-2xl font-bold md:whitespace-nowrap">Thank you for your submission</h1>
      <p v-if="!finished">Processing may take a while. We appreciate your patience.</p>
      <p v-if="finished">Your simulation(s) finished processing. View the result(s) below.</p>
      <!--<USeparator class="w-full mt-4 mb-4" />-->
      <UAlert
        v-if="!finished"
        class="w-full mt-4 p-2 flex flex-col lg:flex-row items-center justify-center gap-[16px]"
        title="This page updates automatically."
        :actions="[
          {
            disabled: seconds_remaining == 0,
            loading: seconds_remaining == 0,
            color: 'primary',
            label: seconds_remaining > 0 ? 'Check Now' : 'Checking...',
            onClick: check_status

          }
        ]"
        icon="i-lucide-info"
        orientation="horizontal"
        variant="subtle"
        color="secondary"
        :ui="{
          title: 'm-0',
          description: 'm-0',
        }">
        <template #description>
          <small v-if="seconds_remaining > 0"><em>Next status check in {{seconds_remaining}} seconds</em></small>
          <small v-if="seconds_remaining == 0"><em><Icon name="i-svg-spinners:ring-resize" />&nbsp;Checking for updates...</em></small>
        </template>
      </UAlert>
      <!--<USeparator class="my-4"></USeparator>-->
      <section id="job_statuses" class="w-full grid items-stretch grid-cols-1 sm:grid-cols-2 sm:min-w-[600px] md:grid-cols-3 gap-4 p-4 md:w-max md:min-w-[700px] md:max-w-full lg:min-w-[900px] rounded-lg border border-gray-200 bg-white" :class="{'mt-4': finished}">
        <div id="job_status" class="flex flex-col justify-between gap-4 flex-1 w-full border border-gray-200 bg-neutral-50/50 px-3 py-2 rounded-lg" v-for="job of processing_status.jobs">
          <div class="w-full flex flex-col gap-1">
            <UTooltip :text="`Job #${job.job_id}`" :delay-duration="0">
              <small class="opacity-70 cursor-pointer text-left overflow-hidden whitespace-nowrap text-ellipsis">Job #{{job.job_id}}</small>
            </UTooltip>

            <div class="flex items-start justify-between gap-4">
              <div class="flex items-center justify-between gap-4 flex-1">
                <h3 class="text-lg font-bold">{{job.simulator_id}}</h3>
                <small>v{{job.version}}</small>
              </div>
            </div>

            <div class="flex items-center gap-2 text-sm font-semibold pl-1">
              <UChip :color="job.status == 'processing' ? 'info' : (job.status == 'success' ? 'success' : 'error')"></UChip>
              <p>Status: {{job.status}}</p>
            </div>
          </div>

          <div class="w-full flex items-center gap-2" v-if="job.status !== 'processing'">
            <UButton class="flex-1" type="button" :to="`https://biosimulations.org/runs/${job.biosimulations_run_id}`" target="_blank" variant="subtle" color="neutral" size="sm" icon="i-lucide-external-link" :label="job.status == 'success' ? 'See Result' : 'More Info'"></UButton>
            <!--<UButton type="button" v-if="job.status == 'success'" :to="`/simulations/browse/${job.biosimulations_run_id}`" target="_blank" variant="subtle" color="neutral" size="sm" icon="i-lucide-external-link" label="Go to Result"></UButton>-->
            <UPopover class="flex-1" mode="hover" :ui="{content: 'p-2 rounded bg-red-50 border border-red-200'}" v-if="job.status == 'failure'">
              <UButton type="button" class="w-full cursor-pointer" variant="subtle" color="error" size="sm" icon="i-fluent-comment-error-24-regular" label="See Error"></UButton>

              <template #content>
                <small class="text-red-400 font-semibold">BioSimulations Run Id: {{job.biosimulations_run_id}}</small>
                <br />
                <small class="text-red-400">{{job.error}}</small>
              </template>
            </UPopover>
          </div>
        </div>
      </section>
    </div>
  </section>
</template>

<style>

</style>
