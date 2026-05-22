<template>
  <div class="flex items-center justify-center min-h-[calc(100vh-3rem)] px-6 py-10">
    <div class="w-full max-w-2xl">
      <div class="text-center mb-8">
        <h1 class="text-xl font-semibold tracking-tight mb-2">
          {{ pipeline?.name ?? 'Demo' }}
        </h1>
        <p class="text-sm text-gray-500 dark:text-gray-400 max-w-xl mx-auto">
          Upload a PDF and we'll extract structured data with citations back to the source.
        </p>
        <div
          v-if="pipeline?.steps?.length"
          class="mt-3 flex items-center justify-center gap-2 text-[11px] text-gray-400"
        >
          <span class="tabular-nums">{{ pipeline.steps.length }} steps</span>
          <span>·</span>
          <span>{{ stepNames }}</span>
          <span>·</span>
          <span>~20–40s typical</span>
        </div>
      </div>

      <label
        class="block glass-subtle rounded-2xl p-10 text-center cursor-pointer border-2 border-dashed border-gray-200 dark:border-white/[0.08] hover:border-primary-400 transition-colors duration-200"
        :class="{ 'opacity-60 pointer-events-none': isUploading }"
      >
        <input
          type="file"
          accept="application/pdf"
          class="sr-only"
          @change="onFileChange"
        />
        <UploadCloud class="h-10 w-10 text-primary-500 mx-auto mb-3" />
        <p class="text-sm font-medium">
          {{ isUploading ? 'Uploading…' : 'Drop a PDF here or click to upload' }}
        </p>
        <p class="text-xs text-gray-400 mt-1">PDF only • up to 25MB • 50 pages max</p>
      </label>

      <p
        v-if="uploadError"
        role="alert"
        class="mt-3 text-xs text-red-500"
      >
        {{ uploadError }}
      </p>

      <div v-if="recentRuns.length" class="mt-8">
        <div class="flex items-center gap-2 mb-3">
          <Clock class="h-3 w-3 text-gray-400" />
          <p class="text-[10px] uppercase tracking-widest text-gray-400">
            Recent runs
          </p>
        </div>
        <ul class="space-y-1.5">
          <li v-for="run in recentRuns" :key="run.pipeline_run_id">
            <button
              type="button"
              class="w-full text-left glass-subtle rounded-lg px-3 py-2 text-xs hover:shadow-sm transition-shadow duration-200 flex items-center gap-2"
              @click="openRun(run.pipeline_run_id)"
            >
              <FileText class="h-3 w-3 text-primary-500 shrink-0" />
              <span class="flex-1 truncate font-medium">
                {{ run.original_filename || run.pipeline_run_id }}
              </span>
              <span class="text-[10px]" :class="statusColor(run.status)">
                {{ run.status }}
              </span>
              <span class="text-[10px] text-gray-400 tabular-nums">
                {{ formatWhen(run.created_at) }}
              </span>
            </button>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Clock, FileText, UploadCloud } from 'lucide-vue-next'
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import type { Pipeline } from '~/types/pipelines'
import { usePipelinesStore } from '~/stores/pipelines'

const props = defineProps<{
  pipeline: Pipeline | null
}>()

const stepNames = computed(() => {
  const names: Record<string, string> = {
    PDF_TO_MD: 'PDF → Markdown',
    LLM_EXTRACT: 'LLM extract',
  }
  return (props.pipeline?.steps ?? [])
    .map((s) => names[s.step_type] ?? s.step_type)
    .join(' → ')
})

const store = usePipelinesStore()
const { isUploading, uploadError, recentRuns } = storeToRefs(store)

function openRun(runId: string) {
  void store.reloadRunIntoWorkspace(runId)
}

function statusColor(s: string) {
  switch (s) {
    case 'completed':
      return 'text-green-600 dark:text-green-400'
    case 'processing':
      return 'text-blue-600 dark:text-blue-400'
    case 'failed':
      return 'text-red-600 dark:text-red-400'
    default:
      return 'text-gray-400'
  }
}

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  const diffMs = Date.now() - d.getTime()
  const m = Math.floor(diffMs / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h`
  return `${Math.floor(h / 24)}d`
}

async function onFileChange(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (file) {
    await store.uploadAndRun(file)
  }
}
</script>
