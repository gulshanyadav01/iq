<template>
  <div ref="containerRef" class="relative w-full h-full overflow-auto bg-gray-50 dark:bg-gray-900">
    <!-- Skeleton while url is missing OR a PDF is still rendering -->
    <div v-if="showSkeleton" class="flex flex-col gap-3 p-3">
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

    <!-- No document -->
    <div v-else-if="!url" class="h-full flex items-center justify-center p-6">
      <div class="text-center space-y-2">
        <UIcon name="i-lucide-file-x" class="w-8 h-8 mx-auto text-gray-300 dark:text-gray-600" />
        <p class="text-xs text-gray-400">No document available</p>
      </div>
    </div>

    <!-- PDF failed to render -->
    <div v-else-if="kind === 'pdf' && renderError" class="h-full flex items-center justify-center p-6">
      <div class="text-center space-y-3">
        <UIcon name="i-lucide-file-warning" class="w-8 h-8 mx-auto text-gray-300 dark:text-gray-600" />
        <p class="text-xs text-gray-400">Couldn't preview this document</p>
        <a
          :href="url"
          target="_blank"
          rel="noopener"
          class="inline-flex items-center gap-1 text-xs font-medium text-primary-600 hover:text-primary-700 dark:text-primary-400 dark:hover:text-primary-300 transition"
        >
          Open document
          <UIcon name="i-lucide-external-link" class="w-3.5 h-3.5" />
        </a>
      </div>
    </div>

    <!-- PDF pages -->
    <div v-else-if="kind === 'pdf'" class="flex flex-col gap-3 p-3">
      <div
        v-for="(page, idx) in pages"
        :key="idx"
        class="relative bg-white shadow-sm mx-auto"
        :style="{ width: page.cssWidth + 'px', height: page.cssHeight + 'px' }"
      >
        <canvas :ref="(el) => setCanvasRef(idx, el as HTMLCanvasElement | null)" />
      </div>
    </div>

    <!-- Image -->
    <div v-else-if="kind === 'image' && !imageError" class="flex items-center justify-center p-3 min-h-full">
      <img :src="url" :alt="filename" class="max-w-full h-auto shadow-sm rounded bg-white" @error="imageError = true">
    </div>

    <!-- Image failed to load -->
    <div v-else-if="kind === 'image' && imageError" class="h-full flex items-center justify-center p-6">
      <div class="text-center space-y-3">
        <UIcon name="i-lucide-image-off" class="w-8 h-8 mx-auto text-gray-300 dark:text-gray-600" />
        <p class="text-xs text-gray-400">Couldn't load this image</p>
        <a
          :href="url"
          target="_blank"
          rel="noopener"
          class="inline-flex items-center gap-1 text-xs font-medium text-primary-600 hover:text-primary-700 dark:text-primary-400 dark:hover:text-primary-300 transition"
        >
          Open document
          <UIcon name="i-lucide-external-link" class="w-3.5 h-3.5" />
        </a>
      </div>
    </div>

    <!-- Fallback (xlsx, csv, unknown) -->
    <div v-else class="h-full flex items-center justify-center p-6">
      <div class="text-center space-y-3">
        <UIcon name="i-lucide-file-text" class="w-8 h-8 mx-auto text-gray-300 dark:text-gray-600" />
        <p class="text-xs text-gray-500 dark:text-gray-400 max-w-[220px] truncate" :title="filename">
          {{ filename || 'Document' }}
        </p>
        <p class="text-xs text-gray-400">This file type can't be previewed inline.</p>
        <a
          :href="url"
          target="_blank"
          rel="noopener"
          class="inline-flex items-center gap-1 text-xs font-medium text-primary-600 hover:text-primary-700 dark:text-primary-400 dark:hover:text-primary-300 transition"
        >
          Open document
          <UIcon name="i-lucide-external-link" class="w-3.5 h-3.5" />
        </a>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import type { PDFDocumentProxy } from 'pdfjs-dist'
import { classifyDocumentKind } from '~/composables/useDocumentKind'

interface PageDims {
  cssWidth: number
  cssHeight: number
}

const props = defineProps<{ url: string | null; filename: string; contentType?: string | null }>()

const containerRef = ref<HTMLElement | null>(null)
const canvasEls = ref<(HTMLCanvasElement | null)[]>([])
const pages = ref<PageDims[]>([])
const renderError = ref(false)
const imageError = ref(false)

// Keep the loaded document around so a resize re-lays-out pages without refetching.
let pdfDoc: PDFDocumentProxy | null = null
let lastRenderWidth = 0
let resizeObserver: ResizeObserver | null = null
let resizeTimer: ReturnType<typeof setTimeout> | null = null
// Bumped on every url change; async load/render steps bail when their token is
// stale so a slow load for a closed/previous document can't paint over a newer one.
let loadToken = 0

const kind = computed(() => classifyDocumentKind(props.filename, props.contentType))

// Show the skeleton while a PDF url is set but pages haven't rendered yet (and no error).
const showSkeleton = computed(
  () => !!props.url && kind.value === 'pdf' && !pages.value.length && !renderError.value,
)

function setCanvasRef(idx: number, el: HTMLCanvasElement | null) {
  canvasEls.value[idx] = el
}

// Lay out and paint every page at the viewer's current width. Renders each
// canvas at devicePixelRatio so PDFs stay crisp on HiDPI/retina screens.
async function renderPages(token = loadToken) {
  if (!pdfDoc) return
  const doc = pdfDoc
  // Fit each page to the viewer's available width. 24px = horizontal padding
  // (p-3 on flex column). Cap the fit scale at 3x so tiny PDFs don't blow up.
  const availableWidth = Math.max((containerRef.value?.clientWidth ?? 800) - 24, 320)
  lastRenderWidth = availableWidth
  const outputScale = Math.min(window.devicePixelRatio || 1, 3)

  const newPages: PageDims[] = []
  const scales: number[] = []
  for (let i = 1; i <= doc.numPages; i++) {
    const page = await doc.getPage(i)
    if (token !== loadToken) return
    const baseViewport = page.getViewport({ scale: 1 })
    const scale = Math.min(availableWidth / baseViewport.width, 3)
    const viewport = page.getViewport({ scale })
    scales.push(scale)
    newPages.push({ cssWidth: viewport.width, cssHeight: viewport.height })
  }
  pages.value = newPages
  await nextTick()
  if (token !== loadToken) return
  for (let i = 0; i < doc.numPages; i++) {
    const canvas = canvasEls.value[i]
    if (!canvas) continue
    const page = await doc.getPage(i + 1)
    if (token !== loadToken) return
    const viewport = page.getViewport({ scale: scales[i] ?? 1 })
    // Backing store at device pixels; CSS box stays at logical pixels → sharp.
    canvas.width = Math.floor(viewport.width * outputScale)
    canvas.height = Math.floor(viewport.height * outputScale)
    canvas.style.width = `${Math.floor(viewport.width)}px`
    canvas.style.height = `${Math.floor(viewport.height)}px`
    const ctx = canvas.getContext('2d')
    if (!ctx) continue
    const transform = outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : undefined
    await page.render({ canvasContext: ctx, viewport, canvas, transform }).promise
  }
}

// Lazy-load PDF.js only on the client (it's large and depends on DOM).
async function loadPdf(url: string, token: number) {
  const pdfjs: typeof import('pdfjs-dist') = await import('pdfjs-dist')
  // Vite resolves ?url to a static asset URL the worker can be loaded from.
  // The .mjs build is the only one shipped by pdfjs-dist 4.x.
  const workerModule = await import('pdfjs-dist/build/pdf.worker.min.mjs?url')
  pdfjs.GlobalWorkerOptions.workerSrc = workerModule.default

  const doc = await pdfjs.getDocument({ url }).promise
  // A newer url won the race while we were loading — drop this document.
  if (token !== loadToken) {
    doc.destroy()
    return
  }
  pdfDoc = doc
  await renderPages(token)
}

// Re-render when the pane width changes (e.g. the slide-over finishes opening),
// so the canvas always matches the displayed size instead of being upscaled.
function ensureResizeObserver() {
  if (resizeObserver || !containerRef.value) return
  resizeObserver = new ResizeObserver(() => {
    const w = Math.max((containerRef.value?.clientWidth ?? 0) - 24, 320)
    if (!pdfDoc || Math.abs(w - lastRenderWidth) < 24) return
    if (resizeTimer) clearTimeout(resizeTimer)
    resizeTimer = setTimeout(() => {
      renderPages().catch((e) => console.warn('[DocumentPreview] re-render failed:', e))
    }, 150)
  })
  resizeObserver.observe(containerRef.value)
}

watch(
  () => props.url,
  async (url) => {
    // Reset state on every url change and invalidate any in-flight load/render.
    const token = ++loadToken
    pages.value = []
    canvasEls.value = []
    renderError.value = false
    imageError.value = false
    lastRenderWidth = 0
    // Free the previous pdfjs document + worker before loading the next one.
    const previous = pdfDoc
    pdfDoc = null
    if (previous) previous.destroy()
    if (url && kind.value === 'pdf') {
      await nextTick()
      ensureResizeObserver()
      try {
        await loadPdf(url, token)
      } catch (e) {
        if (token === loadToken) {
          console.warn('[DocumentPreview] PDF render failed:', e)
          renderError.value = true
        }
      }
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  if (resizeTimer) clearTimeout(resizeTimer)
  loadToken++
  pdfDoc?.destroy()
  pdfDoc = null
})
</script>
