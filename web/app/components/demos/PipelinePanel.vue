<template>
  <div class="h-full flex flex-col bg-white/40 dark:bg-gray-950/40 backdrop-blur-sm">
    <!-- Header: recent runs -->
    <div class="px-4 py-3 border-b border-gray-200/60 dark:border-white/[0.07] flex items-center gap-2">
      <span class="text-[10px] uppercase tracking-widest text-gray-400">Recent runs</span>
      <div class="flex-1" />
      <UDropdownMenu :items="recentRunItems" v-if="recentRuns.length">
        <button class="text-xs px-2 py-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-900 flex items-center gap-1.5 transition-colors duration-200">
          <Clock class="h-3 w-3" />
          <span>Recent</span>
          <span class="tabular-nums text-gray-400">{{ recentRuns.length }}</span>
        </button>
      </UDropdownMenu>
    </div>

    <!-- Step 1 -->
    <div class="px-4 py-3 border-b border-gray-200/60 dark:border-white/[0.07]">
      <div class="flex items-center gap-2 mb-1.5">
        <FileText class="h-3.5 w-3.5 text-primary-500" />
        <span class="text-xs font-semibold">PDF → Markdown</span>
        <span class="ml-auto text-[10px]" :class="statusColor(pdfStepStatus)">{{ pdfStepStatus }}</span>
      </div>
      <p v-if="pdfStepStatus === 'processing'" class="text-xs text-gray-400">
        Converting via Minerva… typically ~15–30s for a 5-page PDF.
      </p>
      <p v-else-if="pdfStepStatus === 'skipped_cached'" class="text-xs text-gray-400">
        Re-using parent run's markdown (live diff).
      </p>
      <p v-else-if="pdfStepStatus === 'completed' && !hasBbox" class="text-xs text-amber-600 dark:text-amber-400">
        Highlights unavailable for this document.
      </p>
    </div>

    <!-- Step 2 -->
    <div class="px-4 py-3 flex-1 min-h-0 flex flex-col">
      <div class="flex items-center gap-2 mb-2">
        <Sparkles class="h-3.5 w-3.5 text-primary-500" />
        <span class="text-xs font-semibold">Extract with prompt</span>
        <span class="ml-auto text-[10px]" :class="statusColor(llmStepStatus)">{{ llmStepStatus }}</span>
      </div>

      <textarea
        v-model="localPrompt"
        rows="6"
        class="text-xs font-mono w-full resize-none rounded-md border border-gray-200 dark:border-white/[0.08] p-2 bg-white/60 dark:bg-gray-950/60"
        :placeholder="'Optional instructions to narrow extraction (e.g. “only invoice number and biller name”). Leave blank to extract every visible label-value pair.'"
        @input="onPromptInput"
      />

      <div class="mt-2 flex items-center gap-2">
        <button
          type="button"
          class="text-xs px-2.5 py-1 rounded-md bg-primary-500 text-white hover:bg-primary-600 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5 transition-colors duration-200"
          :disabled="!isPromptDirty || isReextracting || !currentRun"
          @click="onRunExtraction"
        >
          <Play class="h-3 w-3" />
          {{ isReextracting ? 'Extracting…' : 'Run extraction' }}
        </button>
        <button
          v-if="isPromptDirty"
          type="button"
          class="text-xs px-2 py-1 rounded-md text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-900 transition-colors duration-200"
          title="Revert text box to last-applied prompt"
          @click="resetPrompt"
        >
          Revert
        </button>
        <button
          v-if="canResetToDefault"
          type="button"
          class="text-xs px-2 py-1 rounded-md text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-900 transition-colors duration-200"
          :title="`Reset to pipeline default${defaultPrompt ? '' : ' (empty — extract every field)'}`"
          @click="useDefaultPrompt"
        >
          Reset to default
        </button>
        <span v-if="isPromptDirty" class="text-[10px] text-gray-400 ml-auto">
          Unsaved prompt changes
        </span>
      </div>

      <!-- Parse-degraded banner -->
      <p
        v-if="parseStatus === 'degraded'"
        role="alert"
        class="mt-2 text-xs text-amber-600 dark:text-amber-400"
      >
        Model output couldn't be parsed cleanly. Try a more specific prompt.
      </p>

      <!-- Extracted fields -->
      <div class="mt-3 flex-1 min-h-0 overflow-auto" :class="{ 'opacity-60': isReextracting }">
        <p
          v-if="isReextracting"
          class="text-[10px] text-gray-400 mb-2"
        >
          ← Reflects previous prompt — re-extracting…
        </p>

        <div v-if="!fieldEntries.length" class="text-xs text-gray-400">
          {{ runStatus === 'completed' ? 'No fields extracted.' : 'Fields will appear after extraction.' }}
        </div>

        <ul class="space-y-2.5">
          <li
            v-for="[field, value] in fieldEntries"
            :key="field"
            class="group"
            @mouseenter="hoveredField = field"
            @mouseleave="hoveredField = null"
          >
            <div class="text-[10px] uppercase tracking-widest text-gray-400 mb-0.5">
              {{ field }}
            </div>
            <div class="flex items-center gap-2 flex-wrap">
              <button
                v-if="primaryCitation(field)"
                type="button"
                class="text-sm text-left rounded-md px-1.5 -mx-1.5 py-0.5 border-b border-dashed cursor-pointer transition-colors duration-200"
                :class="isActiveCitationField(field)
                  ? 'border-primary-500 bg-primary-500/10 text-primary-700 dark:text-primary-300'
                  : 'border-gray-300 dark:border-gray-700 text-gray-800 dark:text-gray-100 hover:border-primary-400 hover:bg-primary-500/5'"
                :aria-label="`View source of ${field} in PDF`"
                @click="$emit('citation-click', primaryCitation(field)!)"
              >
                {{ value || '—' }}
              </button>
              <span
                v-else
                class="text-sm text-gray-800 dark:text-gray-100"
              >
                {{ value || '—' }}
              </span>

              <button
                v-for="(bid, idx) in extraCitations(field)"
                :key="bid"
                type="button"
                class="inline-flex items-center gap-0.5 text-[10px] px-1 py-0.5 rounded text-gray-400 hover:text-primary-600 dark:hover:text-primary-300 hover:bg-primary-500/10 transition-colors duration-200"
                :aria-label="`Additional source ${idx + 2} for ${field}`"
                @click="$emit('citation-click', bid)"
              >
                +{{ idx + 2 }}
              </button>

              <span
                v-if="primaryCitation(field)"
                class="text-[10px] text-gray-400 tabular-nums ml-auto opacity-0 group-hover:opacity-100 transition-opacity duration-200"
              >
                {{ pageLabelFor(primaryCitation(field)!) }}
              </span>
            </div>
          </li>
        </ul>
      </div>

      <!-- Footer: tokens + cost -->
      <div
        v-if="currentRun?.result?.cost_usd != null"
        class="mt-3 pt-2 border-t border-gray-200/60 dark:border-white/[0.07] text-[10px] text-gray-400 flex items-center gap-3 tabular-nums"
      >
        <span>{{ currentRun.result.input_tokens ?? 0 }} in / {{ currentRun.result.output_tokens ?? 0 }} out</span>
        <span>${{ (currentRun.result.cost_usd ?? 0).toFixed(5) }}</span>
        <span class="ml-auto font-mono">{{ currentRun.result.model }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Clock, FileText, Play, Sparkles } from 'lucide-vue-next'
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'

import type { Block, StepRunStatus } from '~/types/pipelines'
import { usePipelinesStore } from '~/stores/pipelines'

const props = defineProps<{ activeBlockId?: string | null }>()
const emit = defineEmits<{ 'citation-click': [string] }>()

const hoveredField = ref<string | null>(null)

const store = usePipelinesStore()
const {
  currentRun,
  currentPipeline,
  recentRuns,
  blocks,
  promptDraft,
  hasBbox,
  extractedFields,
  citations,
  isExtracting,
  latestSeq,
  confirmedSeq,
} = storeToRefs(store)

const defaultPrompt = computed<string>(() => {
  const step = currentPipeline.value?.steps.find(
    (s) => s.step_type === 'LLM_EXTRACT',
  )
  return String((step?.config?.user_instruction as string | undefined) ?? '')
})

const canResetToDefault = computed(
  () => localPrompt.value !== defaultPrompt.value,
)

function useDefaultPrompt() {
  localPrompt.value = defaultPrompt.value
}

const localPrompt = ref(promptDraft.value)

watch(promptDraft, (v) => {
  // External resets (e.g. switching pipelines) sync the local field.
  if (v !== localPrompt.value) localPrompt.value = v
})

function onPromptInput(e: Event) {
  localPrompt.value = (e.target as HTMLTextAreaElement).value
}

const isPromptDirty = computed(() => localPrompt.value !== promptDraft.value)

function onRunExtraction() {
  if (!isPromptDirty.value) return
  store.runExtractionNow(localPrompt.value)
}

function resetPrompt() {
  localPrompt.value = promptDraft.value
}

const pdfStepStatus = computed<StepRunStatus | '—'>(() => {
  const s = currentRun.value?.step_runs.find((s) => s.step_type === 'PDF_TO_MD')
  return (s?.status as StepRunStatus | undefined) ?? '—'
})
const llmStepStatus = computed<StepRunStatus | '—'>(() => {
  const s = currentRun.value?.step_runs.find((s) => s.step_type === 'LLM_EXTRACT')
  return (s?.status as StepRunStatus | undefined) ?? '—'
})
const parseStatus = computed(() => currentRun.value?.result?.parse_status ?? null)
const runStatus = computed(() => currentRun.value?.status ?? null)
const isReextracting = computed(
  () => latestSeq.value > confirmedSeq.value || llmStepStatus.value === 'processing',
)

const fieldEntries = computed(() =>
  Object.entries(extractedFields.value as Record<string, string>),
)

function citationsFor(field: string): string[] {
  const map = citations.value as Record<string, string[]>
  return map[field] ?? []
}

function primaryCitation(field: string): string | undefined {
  return citationsFor(field)[0]
}

function extraCitations(field: string): string[] {
  return citationsFor(field).slice(1)
}

function isActiveCitationField(field: string): boolean {
  const primary = primaryCitation(field)
  if (!primary) return false
  return citationsFor(field).includes(props.activeBlockId ?? '')
}

function pageLabelFor(blockId: string): string {
  const b = (blocks.value as Block[]).find((x) => x.block_id === blockId)
  if (!b) return blockId
  const base = `p.${b.page_idx + 1}`
  return b.row_idx != null ? `${base} · row ${b.row_idx + 1}` : base
}

function statusColor(s: string) {
  switch (s) {
    case 'completed':
      return 'text-green-600 dark:text-green-400'
    case 'processing':
      return 'text-blue-600 dark:text-blue-400'
    case 'failed':
      return 'text-red-600 dark:text-red-400'
    case 'skipped_cached':
      return 'text-gray-500 dark:text-gray-400'
    default:
      return 'text-gray-400'
  }
}

const recentRunItems = computed(() =>
  [recentRuns.value.map((r) => ({
    label: `${r.original_filename || r.pipeline_run_id} (${r.status})`,
    onSelect: () => store.reloadRunIntoWorkspace(r.pipeline_run_id),
  }))],
)
</script>
