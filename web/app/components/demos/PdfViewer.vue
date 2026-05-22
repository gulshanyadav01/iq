<template>
  <div ref="containerRef" class="relative w-full h-full overflow-auto bg-gray-50 dark:bg-gray-900">
    <!-- Skeleton while pdfUrl missing OR PDF.js is still rendering pages -->
    <div v-if="!pdfUrl || !pages.length" class="flex flex-col gap-3 p-3">
      <div
        class="relative mx-auto bg-white dark:bg-gray-950 shadow-sm overflow-hidden"
        style="width: 100%; max-width: 560px; aspect-ratio: 1 / 1.414;"
      >
        <div class="absolute inset-0 p-6 flex flex-col gap-3">
          <div class="h-3 w-2/3 rounded bg-gray-100 dark:bg-gray-800 animate-pulse" />
          <div class="h-3 w-5/6 rounded bg-gray-100 dark:bg-gray-800 animate-pulse" />
          <div class="h-3 w-3/4 rounded bg-gray-100 dark:bg-gray-800 animate-pulse" />
          <div class="h-24 mt-2 rounded bg-gray-100 dark:bg-gray-800 animate-pulse" />
          <div class="h-3 w-4/6 rounded bg-gray-100 dark:bg-gray-800 animate-pulse" />
          <div class="h-3 w-3/5 rounded bg-gray-100 dark:bg-gray-800 animate-pulse" />
        </div>
      </div>
    </div>
    <div v-else class="flex flex-col gap-3 p-3">
      <div
        v-for="(page, idx) in pages"
        :key="idx"
        :ref="(el) => setPageRef(idx, el as HTMLElement | null)"
        class="relative bg-white shadow-sm mx-auto"
        :style="{ width: page.cssWidth + 'px', height: page.cssHeight + 'px' }"
      >
        <canvas :ref="(el) => setCanvasRef(idx, el as HTMLCanvasElement | null)" />
        <div
          v-for="hl in highlightsForPage(idx)"
          :key="hl.blockId"
          class="absolute pointer-events-none"
          :style="{
            left: hl.rect.left + 'px',
            top: hl.rect.top + 'px',
            width: hl.rect.width + 'px',
            height: hl.rect.height + 'px',
            background: 'rgba(250, 204, 21, 0.45)',
            outline: '2px solid rgb(202, 138, 4)',
            transition: 'opacity 200ms',
            opacity: hl.blockId === activeBlockId ? 0.9 : 0.4,
          }"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'

import { artifactUrl, getAuthToken } from '~/api/pipelines'
import { usePdfHighlight, type PdfPageDims } from '~/composables/usePdfHighlight'
import type { Block } from '~/types/pipelines'
import { usePipelinesStore } from '~/stores/pipelines'

const props = defineProps<{ activeBlockId: string | null }>()

const store = usePipelinesStore()
const { currentRun, blocks } = storeToRefs(store)
const { configure, rectFor, space } = usePdfHighlight()

const containerRef = ref<HTMLElement | null>(null)
const pageEls = ref<(HTMLElement | null)[]>([])
const canvasEls = ref<(HTMLCanvasElement | null)[]>([])
const pages = ref<PdfPageDims[]>([])

const pdfUrl = computed(() =>
  currentRun.value
    ? artifactUrl(currentRun.value.pipeline_run_id, 'pdf')
    : null,
)

function setPageRef(idx: number, el: HTMLElement | null) {
  pageEls.value[idx] = el
}
function setCanvasRef(idx: number, el: HTMLCanvasElement | null) {
  canvasEls.value[idx] = el
}

function highlightsForPage(idx: number) {
  if (!pages.value[idx] || !props.activeBlockId) return []
  const b = (blocks.value as Block[]).find(
    (x) => x.block_id === props.activeBlockId && x.page_idx === idx,
  )
  if (!b || !b.bbox) return []
  const rect = rectFor(b, pages.value[idx])
  return rect ? [{ blockId: b.block_id, rect }] : []
}

// Lazy-load PDF.js only on the client (it's large and depends on DOM).
async function renderPdf(url: string) {
  const pdfjs: typeof import('pdfjs-dist') = await import('pdfjs-dist')
  // Vite resolves ?url to a static asset URL the worker can be loaded from.
  // The .mjs build is the only one shipped by pdfjs-dist 4.x.
  const workerModule = await import('pdfjs-dist/build/pdf.worker.min.mjs?url')
  pdfjs.GlobalWorkerOptions.workerSrc = workerModule.default

  const token = await getAuthToken()
  const loadingTask = pdfjs.getDocument({
    url,
    withCredentials: true,
    httpHeaders: token ? { Authorization: `Bearer ${token}` } : {},
  })
  const pdf = await loadingTask.promise

  // Fit each page to the viewer's available width. 24px = horizontal padding
  // (p-3 on flex column). Cap scale at 2x so tiny PDFs don't blow up.
  const availableWidth = Math.max(
    (containerRef.value?.clientWidth ?? 800) - 24,
    320,
  )

  const newPages: PdfPageDims[] = []
  const scales: number[] = []
  for (let i = 1; i <= pdf.numPages; i++) {
    const page = await pdf.getPage(i)
    const baseViewport = page.getViewport({ scale: 1 })
    const scale = Math.min(availableWidth / baseViewport.width, 2)
    const viewport = page.getViewport({ scale })
    scales.push(scale)
    newPages.push({
      widthPts: baseViewport.width,
      heightPts: baseViewport.height,
      cssWidth: viewport.width,
      cssHeight: viewport.height,
    })
  }
  pages.value = newPages
  if (newPages[0]) {
    configure(blocks.value as Block[], newPages[0].widthPts)
  }
  await nextTick()
  for (let i = 0; i < pdf.numPages; i++) {
    const canvas = canvasEls.value[i]
    if (!canvas) continue
    const page = await pdf.getPage(i + 1)
    const viewport = page.getViewport({ scale: scales[i] ?? 1 })
    canvas.width = viewport.width
    canvas.height = viewport.height
    const ctx = canvas.getContext('2d')
    if (!ctx) continue
    await page.render({ canvasContext: ctx, viewport, canvas }).promise
  }
}

watch(
  pdfUrl,
  async (url) => {
    if (url) {
      try {
        await renderPdf(url)
      } catch (e) {

        console.warn('[PdfViewer] PDF render failed:', e)
      }
    } else {
      pages.value = []
    }
  },
  { immediate: true },
)

defineExpose({
  scrollToBlock(blockId: string) {
    const b = (blocks.value as Block[]).find((x) => x.block_id === blockId)
    if (!b) return
    const el = pageEls.value[b.page_idx]
    if (!el || !containerRef.value || !pages.value[b.page_idx]) return
    const rect = rectFor(b, pages.value[b.page_idx])
    if (!rect) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      return
    }
    const targetY = el.offsetTop + rect.top - 80
    containerRef.value.scrollTo({ top: targetY, behavior: 'smooth' })
  },
})
</script>
