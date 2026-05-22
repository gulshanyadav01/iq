<template>
  <DemosDemoErrorBoundary @retry="onRetry">
    <DemosUploadHero
      v-if="!currentRun"
      :pipeline="currentPipeline"
    />

    <div v-else class="h-[calc(100vh-3rem)] flex flex-col">
      <!-- Workspace toolbar -->
      <div class="h-10 px-4 flex items-center gap-3 border-b border-gray-200/60 dark:border-white/[0.07] text-xs">
        <span
          class="font-medium truncate max-w-[40ch]"
          :title="currentRun.original_filename"
        >
          {{ currentRun.original_filename }}
        </span>
        <span
          class="inline-flex items-center gap-1.5 px-1.5 py-0.5 rounded-md text-[10px] font-medium"
          :class="statusChip(currentRun.status)"
        >
          <span
            class="h-1.5 w-1.5 rounded-full"
            :class="statusDot(currentRun.status)"
          />
          {{ currentRun.status }}
        </span>
        <div class="flex-1" />
        <button
          type="button"
          class="px-2 py-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-900 flex items-center gap-1.5 transition-colors duration-200"
          @click="onRetry"
        >
          <RotateCcw class="h-3 w-3" />
          New PDF
        </button>
      </div>

      <!-- Responsive: xl=3-col, lg=2-col tabbed, <lg=stacked -->
      <div class="flex-1 min-h-0 hidden xl:grid xl:grid-cols-3 gap-0">
        <DemosPdfViewer
          ref="pdfViewerRef"
          :active-block-id="activeBlockId"
          class="border-r border-gray-200/60 dark:border-white/[0.07]"
        />
        <DemosMdViewer
          ref="mdViewerRef"
          :active-block-id="activeBlockId"
          class="border-r border-gray-200/60 dark:border-white/[0.07]"
        />
        <DemosPipelinePanel :active-block-id="activeBlockId" @citation-click="onCitationClick" />
      </div>

      <!-- Below xl: tab toggle -->
      <div class="flex-1 min-h-0 xl:hidden flex flex-col">
        <div class="px-3 py-2 flex items-center gap-1 border-b border-gray-200/60 dark:border-white/[0.07]">
          <button
            v-for="t in tabs"
            :key="t.id"
            type="button"
            class="text-xs px-2.5 py-1 rounded-md transition-colors duration-200"
            :class="activeTab === t.id ? 'bg-primary-100 dark:bg-primary-500/20 text-primary-700 dark:text-primary-300' : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-900'"
            @click="activeTab = t.id"
          >
            {{ t.label }}
          </button>
        </div>
        <div class="flex-1 min-h-0">
          <DemosPdfViewer
            v-if="activeTab === 'pdf'"
            ref="pdfViewerRef"
            :active-block-id="activeBlockId"
          />
          <DemosMdViewer
            v-else-if="activeTab === 'md'"
            ref="mdViewerRef"
            :active-block-id="activeBlockId"
          />
          <DemosPipelinePanel v-else @citation-click="onCitationClick" />
        </div>
      </div>
    </div>
  </DemosDemoErrorBoundary>
</template>

<script setup lang="ts">
import { RotateCcw } from 'lucide-vue-next'
import { storeToRefs } from 'pinia'
import { computed, onMounted, ref } from 'vue'
import { usePipelinesStore } from '~/stores/pipelines'

definePageMeta({
  layout: 'demos',
  middleware: 'auth',
})

const route = useRoute()
const store = usePipelinesStore()
const { currentPipeline, currentRun } = storeToRefs(store)

const slug = computed(() => String(route.params.slug))
const activeBlockId = ref<string | null>(null)
const activeTab = ref<'pdf' | 'md' | 'fields'>('pdf')

const tabs = [
  { id: 'pdf' as const, label: 'PDF' },
  { id: 'md' as const, label: 'Markdown' },
  { id: 'fields' as const, label: 'Fields' },
]

const pdfViewerRef = ref<{ scrollToBlock: (id: string) => void } | null>(null)
const mdViewerRef = ref<{ scrollToBlock: (id: string) => void } | null>(null)

onMounted(async () => {
  await store.loadPipeline(slug.value)
  await store.loadRecentRuns()
})

function onCitationClick(blockId: string) {
  // Toggle off if same chip clicked again, else activate + scroll.
  if (activeBlockId.value === blockId) {
    activeBlockId.value = null
    return
  }
  activeBlockId.value = blockId
  pdfViewerRef.value?.scrollToBlock(blockId)
  mdViewerRef.value?.scrollToBlock(blockId)
}

function onRetry() {
  store.resetWorkspace()
}

function statusChip(s: string) {
  switch (s) {
    case 'completed':
      return 'bg-green-500/10 text-green-700 dark:text-green-300'
    case 'processing':
      return 'bg-blue-500/10 text-blue-700 dark:text-blue-300'
    case 'failed':
      return 'bg-red-500/10 text-red-700 dark:text-red-300'
    default:
      return 'bg-gray-500/10 text-gray-500 dark:text-gray-400'
  }
}

function statusDot(s: string) {
  switch (s) {
    case 'completed':
      return 'bg-green-500'
    case 'processing':
      return 'bg-blue-500 animate-pulse'
    case 'failed':
      return 'bg-red-500'
    default:
      return 'bg-gray-400'
  }
}
</script>
