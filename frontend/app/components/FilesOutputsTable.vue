<script setup lang="ts">
import { computed } from 'vue'
import type { SimulationRunSummary, ProjectFile } from '~/models/simulation'

const props = defineProps<{
  summary?: SimulationRunSummary
  files?: ProjectFile[]
}>()

const columns = [
  { accessorKey: 'name', header: 'Name' },
  { accessorKey: 'format', header: 'Format' },
  { accessorKey: 'size', header: 'Size' },
  { accessorKey: 'download', header: 'Download' }
]

const tableData = computed(() => {
  if (!props.summary || !props.files) return []

  const config = useRuntimeConfig()

  const formattedProjectSize = (props.summary.run?.projectSize !== undefined)
    ? formatSize(props.summary.run.projectSize)
    : 'N/A';

  const formattedResultsSize = (props.summary.run?.resultsSize !== undefined)
    ? formatSize(props.summary.run.resultsSize)
    : 'N/A';

  function buildTree(files: ProjectFile[]) {
    const root = { children: {} as any }
    for (const f of files) {
      const parts = f.location.split('/')
      let current = root
      for (let i = 0; i < parts.length; i++) {
        const part = parts[i]
        if (!current.children[part]) {
          current.children[part] = {
            name: part,
            children: i === parts.length - 1 ? null : {}
          }
        }
        if (i === parts.length - 1) {
          Object.assign(current.children[part], {
            format: formatName(f.format),
            size: formatSize(f.size),
            downloadUrl: f.url,
            isDir: false
          })
        } else {
          current.children[part].isDir = true
        }
        current = current.children[part]
      }
    }

    function convertToArray(node: any): any[] | undefined {
      if (!node.children) return undefined
      return Object.values(node.children).map((child: any) => ({
        name: child.name,
        format: child.isDir ? 'Directory' : child.format,
        size: child.isDir ? '' : child.size,
        downloadUrl: child.downloadUrl,
        children: child.children ? convertToArray(child) : undefined,
      }))
    }

    return convertToArray(root) || []
  }

  const specChildren = buildTree(props.files)

  const outputChildren = [
    {
      name: 'outputs.json',
      format: 'JSON',
      size: 'N/A',
      downloadUrl: `${config.public.legacy_api_url}/results/${props.summary.id}?includeData=true`
    },
    {
      name: 'outputs.zip',
      format: 'ZIP archive',
      size: formattedResultsSize,
      downloadUrl: `${config.public.legacy_api_url}/results/${props.summary.id}/download`
    },
    {
      name: 'log.json',
      format: 'JSON',
      size: 'N/A',
      downloadUrl: `${config.public.legacy_api_url}/logs/${props.summary.id}`
    }
  ]

  return [
    {
      name: 'Simulation Specification (COMBINE/OMEX archive)',
      format: 'COMBINE/OMEX archive',
      size: formattedProjectSize,
      downloadUrl: `${config.public.legacy_api_url}/runs/${props.summary.id}/download`,
      children: specChildren
    },
    {
      name: 'Simulation Outputs',
      format: 'ZIP archive',
      size: formattedResultsSize,
      downloadUrl: `${config.public.legacy_api_url}/results/${props.summary.id}/download`,
      children: outputChildren
    }
  ]
})

function formatSize(valueBytes: number) {
  if (valueBytes === 0) return '0 B';
  const base = 1000;
  let quantity = valueBytes;
  let suffix = 'B';

  if (valueBytes >= base ** 5) {
    quantity = valueBytes / base ** 5
    suffix = 'PB';
  } else if (valueBytes >= base ** 4) {
    quantity = valueBytes / base ** 4
    suffix = 'TB';
  } else if (valueBytes >= base ** 3) {
    quantity = valueBytes / base ** 3
    suffix = 'GB';
  } else if (valueBytes >= base ** 2) {
    quantity = valueBytes / base ** 2
    suffix = 'MB';
  } else if (valueBytes >= base) {
    quantity = valueBytes / base
    suffix = 'KB';
  }

  let quantityStr;
  if (quantity === Math.round(quantity)) {
    quantityStr = Math.round(quantity).toString();
  } else if (quantity >= 100) {
    quantityStr = quantity.toFixed(1);
  } else if (quantity >= 10) {
    quantityStr = quantity.toFixed(2);
  } else {
    quantityStr = quantity.toFixed(2);
  }
  return quantityStr + ' ' + suffix;
}

async function forceDownload(url: string, filename: string) {
  if (!url) return;
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error('Network response was not ok');
    const blob = await response.blob();
    const windowUrl = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.style.display = 'none';
    a.href = windowUrl;
    a.download = filename || 'download';
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(windowUrl);
    a.remove();
  } catch (err) {
    console.error('Failed to download, falling back to open', err);
    window.open(url, '_blank');
  }
}

function formatName(formatUri: string) {
  if (!formatUri) return 'Unknown'
  const lower = formatUri.toLowerCase()
  if (lower.includes('sed-ml')) return 'SED-ML'
  if (lower.includes('sbml')) return 'SBML'
  if (lower.includes('omex-metadata')) return 'OMEX Metadata'
  if (lower.includes('json')) return 'JSON'
  if (lower.includes('x-hdf')) return 'HDF5'
  if (lower.includes('image')) return 'Image'
  if (lower.includes('zip')) return 'ZIP archive'
  return formatUri.split('/').pop() || 'Unknown'
}

function getIconForFile(row: any) {
  if (row.children) {
    return 'i-lucide-folder'
  }
  const f = (row.format || '').toLowerCase()
  if (f.includes('sed-ml')) return 'i-lucide-file-code'
  if (f.includes('sbml')) return 'i-lucide-file-text'
  if (f.includes('json')) return 'i-lucide-file-json'
  if (f.includes('hdf5')) return 'i-lucide-database'
  if (f.includes('image')) return 'i-lucide-image'
  if (f.includes('zip')) return 'i-lucide-file-archive'
  if (f.includes('omex metadata')) return 'i-lucide-file-cog'
  return 'i-lucide-file'
}
</script>
<template>
  <div class="border border-neutral-200 rounded-lg overflow-hidden bg-white">
    <UTable
      :columns="columns"
      :data="tableData"
      :get-sub-rows="row => row.children"
      class="w-full"
    >
      <template #name-cell="{ row }">
        <div class="flex items-center gap-2" :style="{ marginLeft: `${row.depth * 1.5}rem` }">
          <UButton
            v-if="row.getCanExpand()"
            color="neutral"
            variant="ghost"
            :icon="row.getIsExpanded() ? 'i-lucide-chevron-down' : 'i-lucide-chevron-right'"
            size="xs"
            class="w-5 h-5 flex items-center justify-center p-0 cursor-pointer"
            @click="row.toggleExpanded()"
          />
          <div v-else class="w-5 h-5" />

          <UIcon :name="getIconForFile(row.original)" class="w-5 h-5 text-gray-500 shrink-0" />
          <span class="font-medium text-sm text-gray-800 break-all">{{ row.original.name }}</span>
        </div>
      </template>

      <template #download-cell="{ row }">
        <UButton
          v-if="row.original.downloadUrl"
          color="primary"
          variant="soft"
          icon="i-lucide-download"
          class="cursor-pointer"
          size="xs"
          @click="forceDownload(row.original.downloadUrl, row.original.name)"
          label="Download"
        />
      </template>
    </UTable>
  </div>
</template>

<style scoped>
:deep(tr:has(> td[colspan]:empty)) {
  display: none !important;
}
</style>
