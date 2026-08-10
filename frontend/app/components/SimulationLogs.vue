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

        <LogAlgorithm v-if="docLog.algorithm" :kisao-id="docLog.algorithm" />

        <div v-if="docLog.skipReason" class="mt-2 text-sm text-neutral-600">
          <span class="font-bold">Skipped:</span> {{ docLog.skipReason.message || docLog.skipReason.type || 'No details provided.' }}
        </div>

        <div v-if="docLog.exception" class="mt-2 text-sm text-red-600">
          <span class="font-bold">Exception:</span> {{ docLog.exception.message || docLog.exception.type || 'No details provided.' }}
        </div>

        <div v-if="docLog.output" class="bg-[#1e1e1e] text-[#d4d4d4] p-4 rounded-lg text-sm overflow-x-auto whitespace-pre-wrap font-mono mt-2 leading-relaxed shadow-inner" v-dompurify-html="formatLogOutput(docLog.output)"></div>
        <div v-else class="italic text-neutral-500 mt-2">
          No output log available.
        </div>
      </div>
    </div>

    <div v-if="taskLogs.length > 0" class="flex flex-col gap-4">
      <h3 class="text-lg font-bold">Tasks</h3>
      <div v-for="t in taskLogs" :key="t.docLocation + t.task.id" class="border border-neutral-200 rounded-lg p-4 bg-neutral-50 flex flex-col gap-2">
        <div class="flex items-center gap-2">
          <h4 class="font-bold text-base">{{ t.docLocation }} :: {{ t.task.id }}</h4>
          <span class="text-xs px-2 py-0.5 rounded font-medium text-white" :class="statusColor(t.task.status)">
            {{ t.task.status }}
          </span>
        </div>
        <LogAlgorithm v-if="t.task.algorithm" :kisao-id="t.task.algorithm" />
        <div v-if="t.task.skipReason" class="mt-2 text-sm text-neutral-600">
          <span class="font-bold">Skipped:</span> {{ t.task.skipReason.message || t.task.skipReason.type || 'No details provided.' }}
        </div>
        <div v-if="t.task.exception" class="mt-2 text-sm text-red-600">
          <span class="font-bold">Exception:</span> {{ t.task.exception.message || t.task.exception.type || 'No details provided.' }}
        </div>
        <div v-if="t.task.output" class="bg-[#1e1e1e] text-[#d4d4d4] p-4 rounded-lg text-sm overflow-x-auto whitespace-pre-wrap font-mono mt-2 leading-relaxed shadow-inner" v-dompurify-html="formatLogOutput(t.task.output)"></div>
        <div v-else class="italic text-neutral-500 mt-2">
          No output log available.
        </div>
      </div>
    </div>

    <div v-if="reportLogs.length > 0" class="flex flex-col gap-4">
      <h3 class="text-lg font-bold">Reports</h3>
      <div v-for="r in reportLogs" :key="r.docLocation + r.report.id" class="border border-neutral-200 rounded-lg p-4 bg-neutral-50 flex flex-col gap-2">
        <div class="flex items-center gap-2">
          <h4 class="font-bold text-base">{{ r.docLocation }} :: {{ r.report.id }}</h4>
          <span class="text-xs px-2 py-0.5 rounded font-medium text-white" :class="statusColor(r.report.status)">
            {{ r.report.status }}
          </span>
        </div>
        <LogAlgorithm v-if="r.report.algorithm" :kisao-id="r.report.algorithm" />
        <div v-if="r.report.skipReason" class="mt-2 text-sm text-neutral-600">
          <span class="font-bold">Skipped:</span> {{ r.report.skipReason.message || r.report.skipReason.type || 'No details provided.' }}
        </div>
        <div v-if="r.report.exception" class="mt-2 text-sm text-red-600">
          <span class="font-bold">Exception:</span> {{ r.report.exception.message || r.report.exception.type || 'No details provided.' }}
        </div>
        <div v-if="r.report.output" class="bg-[#1e1e1e] text-[#d4d4d4] p-4 rounded-lg text-sm overflow-x-auto whitespace-pre-wrap font-mono mt-2 leading-relaxed shadow-inner" v-dompurify-html="formatLogOutput(r.report.output)"></div>
        <div v-else class="italic text-neutral-500 mt-2">
          No output log available.
        </div>
      </div>
    </div>

    <div v-if="plotLogs.length > 0" class="flex flex-col gap-4">
      <h3 class="text-lg font-bold">Plots</h3>
      <div v-for="p in plotLogs" :key="p.docLocation + p.plot.id" class="border border-neutral-200 rounded-lg p-4 bg-neutral-50 flex flex-col gap-2">
        <div class="flex items-center gap-2">
          <h4 class="font-bold text-base">{{ p.docLocation }} :: {{ p.plot.id }}</h4>
          <span class="text-xs px-2 py-0.5 rounded font-medium text-white" :class="statusColor(p.plot.status)">
            {{ p.plot.status }}
          </span>
        </div>
        <LogAlgorithm v-if="p.plot.algorithm" :kisao-id="p.plot.algorithm" />
        <div v-if="p.plot.skipReason" class="mt-2 text-sm text-neutral-600">
          <span class="font-bold">Skipped:</span> {{ p.plot.skipReason.message || p.plot.skipReason.type || 'No details provided.' }}
        </div>
        <div v-if="p.plot.exception" class="mt-2 text-sm text-red-600">
          <span class="font-bold">Exception:</span> {{ p.plot.exception.message || p.plot.exception.type || 'No details provided.' }}
        </div>
        <div v-if="p.plot.output" class="bg-[#1e1e1e] text-[#d4d4d4] p-4 rounded-lg text-sm overflow-x-auto whitespace-pre-wrap font-mono mt-2 leading-relaxed shadow-inner" v-dompurify-html="formatLogOutput(p.plot.output)"></div>
        <div v-else class="italic text-neutral-500 mt-2">
          No output log available.
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

        <LogAlgorithm v-if="logs.algorithm" :kisao-id="logs.algorithm" />

        <div v-if="logs.skipReason" class="mt-2 text-sm text-neutral-600">
          <span class="font-bold">Skipped:</span> {{ logs.skipReason.message || logs.skipReason.type || 'No details provided.' }}
        </div>

        <div v-if="logs.exception" class="mt-2 text-sm text-red-600">
          <span class="font-bold">Exception:</span> {{ logs.exception.message || logs.exception.type || 'No details provided.' }}
        </div>

        <div v-if="logs.output" class="bg-[#1e1e1e] text-[#d4d4d4] p-4 rounded-lg text-sm overflow-x-auto whitespace-pre-wrap font-mono mt-2 leading-relaxed shadow-inner" v-dompurify-html="formatLogOutput(logs.output)"></div>
        <div v-else class="italic text-neutral-500 mt-2">
          No output log available.
        </div>
      </div>
    </div>

  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import Anser from 'anser'

function formatLogOutput(output: string) {
  if (!output) return '';
  return Anser.ansiToHtml(output, { use_classes: false });
}

const props = defineProps<{
  logs: any;
}>()

const taskLogs = computed(() => {
  if (!props.logs?.sedDocuments) return [];
  const tasks: any[] = [];
  props.logs.sedDocuments.forEach((docLog: any) => {
    if (docLog.tasks) {
      docLog.tasks.forEach((taskLog: any) => {
        tasks.push({ docLocation: docLog.location, task: taskLog });
      });
    }
  });
  return tasks;
});

const reportLogs = computed(() => {
  if (!props.logs?.sedDocuments) return [];
  const reports: any[] = [];
  props.logs.sedDocuments.forEach((docLog: any) => {
    if (docLog.outputs) {
      docLog.outputs.forEach((outputLog: any) => {
        if ('dataSets' in outputLog) {
          reports.push({ docLocation: docLog.location, report: outputLog });
        }
      });
    }
  });
  return reports;
});

const plotLogs = computed(() => {
  if (!props.logs?.sedDocuments) return [];
  const plots: any[] = [];
  props.logs.sedDocuments.forEach((docLog: any) => {
    if (docLog.outputs) {
      docLog.outputs.forEach((outputLog: any) => {
        if (!('dataSets' in outputLog)) {
          plots.push({ docLocation: docLog.location, plot: outputLog });
        }
      });
    }
  });
  return plots;
});

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
