<template>
  <!--
    Hidden worksheets, listed apart from the sheets that fed extraction.

    A hidden tab is almost always hidden deliberately, so this is reference
    material, not a problem list: muted, collapsed by default, and free of
    warning colour. Interleaving these with the real sheets made a 14-sheet
    workbook read as if 10 things had gone wrong.

    The content peek is kept — it turns "why is this row missing?" from a
    20-minute Excel round trip into a 10-second in-app check.
  -->
  <div v-if="names.length" class="pt-6 mt-6 border-t border-gray-200 dark:border-gray-800">
    <p class="text-[10px] font-semibold uppercase tracking-widest text-gray-400 mb-2">Not extracted</p>
    <details :open="isOpen" @toggle="isOpen = ($event.target as HTMLDetailsElement).open">
      <summary class="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400 cursor-pointer select-none hover:text-gray-700 dark:hover:text-gray-300">
        <UIcon name="i-lucide-eye-off" class="w-3.5 h-3.5 text-gray-400" />
        <span>{{ names.length }} hidden {{ names.length === 1 ? 'sheet' : 'sheets' }} — indexed for reference, not extracted</span>
      </summary>

      <div class="mt-3 space-y-3 pl-1">
        <div v-for="name in names" :key="name" class="space-y-1.5">
          <div class="flex items-center gap-2">
            <span class="text-xs font-medium text-gray-600 dark:text-gray-400 truncate max-w-md" :title="name">{{ name }}</span>
            <span class="px-1.5 py-0.5 rounded text-[10px] font-medium uppercase bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400">
              {{ getSheetClassification(name, indexed) }}
            </span>
            <span class="text-xs text-gray-500 dark:text-gray-400">{{ getSheetVisibility(name, indexed) }}</span>
          </div>
          <!-- Content is rendered only once expanded: markdown for every hidden
               sheet would otherwise be parsed on mount and re-parsed on every
               refresh, for panels nobody opened. -->
          <details @toggle="onPeek(name, $event)">
            <summary class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 cursor-pointer select-none">
              Show content
            </summary>
            <div v-if="peeked.has(name)" class="mt-2 overflow-x-auto max-h-64 overflow-y-auto rounded-lg bg-gray-50 dark:bg-gray-900 p-4">
              <!-- eslint-disable-next-line vue/no-v-html -->
              <div class="prose prose-xs dark:prose-invert max-w-none text-xs whitespace-nowrap [&_table]:border-collapse [&_td]:border [&_td]:border-gray-200 [&_td]:dark:border-gray-700 [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:border-gray-200 [&_th]:dark:border-gray-700 [&_th]:px-2 [&_th]:py-1 [&_th]:bg-gray-100 [&_th]:dark:bg-gray-800" v-html="render(indexed.sheet_contents[name] || '')" />
            </div>
          </details>
        </div>
      </div>
    </details>
  </div>
</template>

<script setup lang="ts">
import MarkdownIt from 'markdown-it'
import { ref, watch } from 'vue'
import {
  getSheetClassification,
  getSheetVisibility,
} from '~/composables/useCommissionIntakeDetail'
import type { IndexedContent } from '~/composables/useCommissionIntakeDetail'

const props = withDefaults(
  defineProps<{
    /** Hidden sheet names, in workbook order. */
    names: string[]
    indexed: IndexedContent
    /**
     * Counter, not a boolean. "View sheets" must re-open the group every time
     * it is clicked, including after the reader has collapsed it by hand — and
     * a boolean cannot express that: once the parent has set it true, setting
     * it true again is not a change, so a watcher never fires and the page
     * scrolls to a still-collapsed group. Each increment is a fresh request.
     */
    openRequest?: number
  }>(),
  { openRequest: 0 }
)

// @toggle writes manual open/close back, so local state can diverge from the
// last request — which is exactly why the trigger has to be a counter.
const isOpen = ref(false)
watch(() => props.openRequest, () => { isOpen.value = true })

const md = new MarkdownIt({ html: false, breaks: false })
const cache = new Map<string, string>()

function render(content: string): string {
  const hit = cache.get(content)
  if (hit !== undefined) return hit
  const html = md.render(content)
  cache.set(content, html)
  return html
}

// Sheets the reader has expanded at least once. Gates the markdown render so a
// workbook with 10 hidden tabs does no parsing until something is opened.
const peeked = ref(new Set<string>())

function onPeek(name: string, event: Event) {
  if ((event.target as HTMLDetailsElement).open) {
    peeked.value = new Set(peeked.value).add(name)
  }
}
</script>
