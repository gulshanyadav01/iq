<template>
  <div ref="containerRef" class="w-full h-full overflow-auto bg-white dark:bg-gray-950 px-4 py-3">
    <div v-if="!blocks.length" class="flex flex-col gap-2 py-1">
      <div class="h-3 w-3/4 rounded bg-gray-100 dark:bg-gray-800 animate-pulse" />
      <div class="h-3 w-11/12 rounded bg-gray-100 dark:bg-gray-800 animate-pulse" />
      <div class="h-3 w-5/6 rounded bg-gray-100 dark:bg-gray-800 animate-pulse" />
      <div class="h-3 w-2/3 rounded bg-gray-100 dark:bg-gray-800 animate-pulse" />
      <div class="h-3 w-4/5 rounded bg-gray-100 dark:bg-gray-800 animate-pulse" />
      <div class="h-3 w-3/5 rounded bg-gray-100 dark:bg-gray-800 animate-pulse" />
      <p class="mt-3 text-[10px] uppercase tracking-widest text-gray-400">
        Awaiting conversion…
      </p>
    </div>
    <div v-else class="prose prose-sm max-w-none dark:prose-invert">
      <div
        v-for="b in blocks"
        :key="b.block_id"
        :data-block-id="b.block_id"
        class="md-block py-1 -mx-2 px-2 rounded transition-colors duration-200"
        :class="{ 'bg-yellow-100/60 dark:bg-yellow-500/10': b.block_id === activeBlockId }"
      >
        <template v-if="(b.type === 'table' || b.type === 'html') && b.html">
          <div class="md-html" v-html="sanitize(b.html)" />
        </template>
        <template v-else-if="b.type === 'image'">
          <em class="text-xs text-gray-400">[Image on page {{ b.page_idx + 1 }}] {{ b.text }}</em>
        </template>
        <template v-else-if="b.type === 'equation' || b.type === 'interline_equation'">
          <code class="text-xs">{{ b.latex || b.text }}</code>
        </template>
        <template v-else>
          <p class="whitespace-pre-wrap text-sm">{{ b.text }}</p>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { storeToRefs } from 'pinia'

import type { Block } from '~/types/pipelines'
import { usePipelinesStore } from '~/stores/pipelines'

const props = defineProps<{ activeBlockId: string | null }>()

const store = usePipelinesStore()
const { blocks } = storeToRefs(store)

const containerRef = ref<HTMLElement | null>(null)

// Minimal sanitizer for HTML tables: strip <script> tags only. Minerva is a
// trusted upstream but we still avoid passing arbitrary script content through.
function sanitize(html: string): string {
  return html.replace(/<script[\s\S]*?<\/script>/gi, '')
}

defineExpose({
  scrollToBlock(blockId: string) {
    if (!containerRef.value) return
    const el = containerRef.value.querySelector(`[data-block-id="${blockId}"]`) as HTMLElement | null
    if (!el) return
    const top = el.offsetTop - 40
    containerRef.value.scrollTo({ top, behavior: 'smooth' })
  },
})
</script>
