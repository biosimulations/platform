<template>
  <div class="flex flex-col gap-6" v-if="logs">

    <div v-if="logs.sedDocuments && logs.sedDocuments.length > 0" class="flex flex-col gap-4">
      <h3 class="text-lg font-bold">Simulation experiments (SED-ML documents)</h3>
      <div v-for="docLog in logs.sedDocuments" :key="docLog.location" class="border border-neutral-200 rounded-lg p-4 bg-neutral-50 flex flex-col gap-2">
        <div class="flex items-center gap-2">
          <h4 class="font-bold text-base">{{ docLog.location }}</h4>
          <span class="text-xs px-2 py-0.5 rounded font-medium text-white" :class="statusColor(docLog.status)">
            {{ docLog.status }}
          </span>
        </div>

        <div v-if="docLog.skipReason" class="mt-2 text-sm text-neutral-600">
          <span class="font-bold">Skipped:</span> {{ docLog.skipReason.message || docLog.skipReason.type || 'No details provided.' }}
        </div>

        <div v-if="docLog.exception" class="mt-2 text-sm text-red-600">
          <span class="font-bold">Exception:</span> {{ docLog.exception.message || docLog.exception.type || 'No details provided.' }}
        </div>

        <div v-if="docLog.output" class="bg-[#1e1e1e] text-[#d4d4d4] p-4 rounded-lg text-sm overflow-x-auto whitespace-pre-wrap font-mono mt-2 leading-relaxed">
          {{ docLog.output }}
        </div>
      </div>
    </div>

    <div class="flex flex-col gap-4">
      <h3 class="text-lg font-bold">Simulation run</h3>
      <div class="border border-neutral-200 rounded-lg p-4 bg-neutral-50 flex flex-col gap-2">
        <div class="flex items-center gap-2">
          <h4 class="font-bold text-base">Raw output of the simulation run</h4>
          <span class="text-xs px-2 py-0.5 rounded font-medium text-white" :class="statusColor(logs.status)">
            {{ logs.status }}
          </span>
        </div>

        <div v-if="logs.skipReason" class="mt-2 text-sm text-neutral-600">
          <span class="font-bold">Skipped:</span> {{ logs.skipReason.message || logs.skipReason.type || 'No details provided.' }}
        </div>

        <div v-if="logs.exception" class="mt-2 text-sm text-red-600">
          <span class="font-bold">Exception:</span> {{ logs.exception.message || logs.exception.type || 'No details provided.' }}
        </div>

        <div v-if="logs.output" class="bg-[#1e1e1e] text-[#d4d4d4] p-4 rounded-lg text-sm overflow-x-auto whitespace-pre-wrap font-mono mt-2 leading-relaxed shadow-inner">
          {{ logs.output }}
        </div>
        <div v-else class="italic text-neutral-500 mt-2">
          No output log available.
        </div>
      </div>
    </div>

  </div>
</template>

<script setup lang="ts">
defineProps<{
  logs: any;
}>()

function statusColor(status: string) {
  if (!status) return 'bg-neutral-500';
  const s = status.toUpperCase();
  if (s === 'SUCCEEDED') return 'bg-green-500';
  if (s === 'RUNNING') return 'bg-yellow-500';
  if (s === 'FAILED') return 'bg-red-500';
  if (s === 'QUEUED') return 'bg-blue-500';
  return 'bg-neutral-500';
}
</script>
