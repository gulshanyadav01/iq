import { defineStore } from 'pinia'
import { computed, ref, shallowRef } from 'vue'

import { artifactUrl, getAuthToken, pipelinesApi } from '~/api/pipelines'
import type {
  Block,
  Pipeline,
  PipelineRun,
  RunStatus,
} from '~/types/pipelines'

const POLL_INTERVAL_MS = 1500
const POLL_STALL_MS = 90_000

const TERMINAL: RunStatus[] = ['completed', 'failed']

export const usePipelinesStore = defineStore('pipelines', () => {
  // ── Pipeline catalogue ────────────────────────────────────────────────
  const pipelines = ref<Pipeline[]>([])
  const currentPipeline = ref<Pipeline | null>(null)
  const isLoadingPipeline = ref(false)

  // ── Active workspace ──────────────────────────────────────────────────
  const currentRun = ref<PipelineRun | null>(null)
  const recentRuns = ref<PipelineRun[]>([])
  const blocks = ref<Block[]>([])
  const isUploading = ref(false)
  const uploadError = ref<string | null>(null)
  const promptDraft = ref('')

  // ── Live-diff race protection ─────────────────────────────────────────
  const latestSeq = ref(0)
  const confirmedSeq = ref(0)
  const inflightAbort = shallowRef<AbortController | null>(null)
  let debounceHandle: ReturnType<typeof setTimeout> | null = null

  // ── Polling ───────────────────────────────────────────────────────────
  let pollHandle: ReturnType<typeof setInterval> | null = null
  let pollStartedAt = 0
  const isPollStalled = ref(false)

  // ── Computed ──────────────────────────────────────────────────────────
  const isExtracting = computed(
    () => latestSeq.value > confirmedSeq.value || isPollActive.value,
  )
  const isPollActive = computed(() => pollHandle !== null)
  const hasBbox = computed(() => {
    const step = currentRun.value?.step_runs.find(
      (s) => s.step_type === 'PDF_TO_MD',
    )
    return Boolean((step?.output as { has_bbox?: boolean } | null)?.has_bbox)
  })
  const extractedFields = computed(
    () => currentRun.value?.result?.extracted_fields ?? {},
  )
  const citations = computed(
    () => currentRun.value?.result?.citations ?? {},
  )

  // ── Catalogue API ─────────────────────────────────────────────────────
  async function loadPipelines() {
    const { data } = await pipelinesApi.list()
    pipelines.value = unwrapList(data)
  }

  async function loadPipeline(slug: string) {
    isLoadingPipeline.value = true
    try {
      const { data } = await pipelinesApi.get(slug)
      if (data) {
        currentPipeline.value = data
        const step2 = data.steps.find((s) => s.step_type === 'LLM_EXTRACT')
        if (step2) {
          promptDraft.value = String(step2.config.user_instruction ?? '')
        }
      }
    } finally {
      isLoadingPipeline.value = false
    }
  }

  async function loadRecentRuns() {
    const { data } = await pipelinesApi.listRuns()
    recentRuns.value = unwrapList(data).slice(0, 10)
  }

  // ── Fresh upload ──────────────────────────────────────────────────────
  async function uploadAndRun(file: File): Promise<void> {
    if (!currentPipeline.value) return
    isUploading.value = true
    uploadError.value = null
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('pipeline_slug', currentPipeline.value.slug)
      const { data, error } = await pipelinesApi.createRun(fd)
      if (error || !data) {
        uploadError.value = (error?.message as string) || 'Upload failed'
        return
      }
      currentRun.value = data
      blocks.value = []
      startPolling(data.pipeline_run_id)
    } finally {
      isUploading.value = false
    }
  }

  // ── Manual re-extraction ──────────────────────────────────────────────
  function runExtractionNow(newPrompt: string) {
    promptDraft.value = newPrompt
    if (debounceHandle) clearTimeout(debounceHandle)
    void fireDiff(newPrompt)
  }

  async function fireDiff(prompt: string) {
    if (!currentRun.value || !currentPipeline.value) return
    if (inflightAbort.value) {
      inflightAbort.value.abort()
    }
    const seq = ++latestSeq.value
    const controller = new AbortController()
    inflightAbort.value = controller

    const llmStep = currentPipeline.value.steps.find(
      (s) => s.step_type === 'LLM_EXTRACT',
    )
    if (!llmStep) return

    const fd = new FormData()
    fd.append('pipeline_slug', currentPipeline.value.slug)
    fd.append('parent_run_id', currentRun.value.pipeline_run_id)
    fd.append(
      'config_overrides',
      JSON.stringify({ [String(llmStep.order)]: { user_instruction: prompt } }),
    )

    try {
      const { data, error } = await pipelinesApi.createRun(fd)
      if (controller.signal.aborted) return
      if (seq < latestSeq.value) return // stale response — drop
      if (error || !data) return
      currentRun.value = data
      confirmedSeq.value = seq
      startPolling(data.pipeline_run_id)
    } catch {
      // ignore aborted/cancelled fetches
    }
  }

  // ── Polling ───────────────────────────────────────────────────────────
  function startPolling(runId: string) {
    stopPolling()
    pollStartedAt = Date.now()
    isPollStalled.value = false
    let blocksLoaded = false
    pollHandle = setInterval(async () => {
      if (Date.now() - pollStartedAt > POLL_STALL_MS) {
        isPollStalled.value = true
      }
      const { data } = await pipelinesApi.getRun(runId)
      if (!data) return
      currentRun.value = data
      // Load MD blocks as soon as PDF_TO_MD step completes, even while
      // LLM_EXTRACT is still running. Lets the user preview the markdown
      // ~30s earlier than waiting for the whole pipeline.
      if (!blocksLoaded) {
        const pdfStep = data.step_runs.find((s) => s.step_type === 'PDF_TO_MD')
        if (pdfStep && (pdfStep.status === 'completed' || pdfStep.status === 'skipped_cached')) {
          blocksLoaded = true
          await loadBlocksFromCurrentRun()
        }
      }
      if (TERMINAL.includes(data.status)) {
        stopPolling()
        if (!blocksLoaded) await loadBlocksFromCurrentRun()
        await loadRecentRuns()
      }
    }, POLL_INTERVAL_MS)
  }

  function stopPolling() {
    if (pollHandle) {
      clearInterval(pollHandle)
      pollHandle = null
    }
  }

  // ── Block loading for the MD pane + highlight overlay ─────────────────
  async function loadBlocksFromCurrentRun() {
    if (!currentRun.value) return
    const url = artifactUrl(currentRun.value.pipeline_run_id, 'blocks')
    try {
      const token = await getAuthToken()
      const headers: Record<string, string> = {}
      if (token) headers.Authorization = `Bearer ${token}`
      const resp = await fetch(url, { credentials: 'include', headers })
      if (!resp.ok) {
        console.warn('[pipelines] blocks fetch failed', resp.status)
        return
      }
      const data = (await resp.json()) as Block[]
      blocks.value = data
    } catch (e) {
      console.warn('[pipelines] blocks fetch error', e)
    }
  }

  // ── Reset ─────────────────────────────────────────────────────────────
  async function reloadRunIntoWorkspace(runId: string) {
    const { data } = await pipelinesApi.getRun(runId)
    if (data) {
      currentRun.value = data
      await loadBlocksFromCurrentRun()
    }
  }

  function resetWorkspace() {
    stopPolling()
    if (inflightAbort.value) inflightAbort.value.abort()
    currentRun.value = null
    blocks.value = []
    latestSeq.value = 0
    confirmedSeq.value = 0
    isPollStalled.value = false
    uploadError.value = null
  }

  return {
    // state
    pipelines,
    currentPipeline,
    isLoadingPipeline,
    currentRun,
    recentRuns,
    blocks,
    isUploading,
    uploadError,
    promptDraft,
    latestSeq,
    confirmedSeq,
    isPollStalled,
    // computed
    isExtracting,
    isPollActive,
    hasBbox,
    extractedFields,
    citations,
    // actions
    loadPipelines,
    loadPipeline,
    loadRecentRuns,
    uploadAndRun,
    runExtractionNow,
    reloadRunIntoWorkspace,
    resetWorkspace,
  }
})

function unwrapList<T>(data: T[] | { results: T[] } | null | undefined): T[] {
  if (!data) return []
  if (Array.isArray(data)) return data
  if (Array.isArray((data as { results?: T[] }).results)) {
    return (data as { results: T[] }).results
  }
  return []
}
