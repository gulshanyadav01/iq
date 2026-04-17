import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'

// ── Types ────────────────────────────────────────────────────────────────────

export interface ValidationIssue {
  severity: string
  category: string
  field_path: string
  message: string
  suggestion: string
}

export interface OverlapIssue {
  severity: string
  overlap_type: string
  product_code: string
  rule1_id: string
  rule2_id: string
  description: string
}

export interface ValidationResult {
  is_valid: boolean
  has_critical: boolean
  has_high: boolean
  schema_issues: ValidationIssue[]
  overlap_issues: OverlapIssue[]
}

export interface CICSResult {
  id: string
  cics_doc: Record<string, unknown>
  warnings: string[]
  errors: string[]
  duration_seconds: number
  sheet_results: unknown[]
}

export interface IndexedContent {
  source_type: string
  primary_content: string
  email_context: string
  sheet_contents: Record<string, string>
  sheet_metadata: Record<string, unknown>
  indexer_version: string
}

export interface ExtractionPass {
  pass_number: 1 | 2 | 3
  sheet_name: string
  attachment_name: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  result: Record<string, unknown> | null
  llm_model_used: string
  duration_seconds: number
  error: string
}

export interface IndexProgress {
  step: string
  detail: string
}

export interface CommissionDocument {
  id: string
  status: 'initiated' | 'indexing' | 'indexed' | 'extracting' | 'completed' | 'failed'
  processing_mode: 'full' | 'index_only' | 'extract_only'
  index_progress: IndexProgress | null
  original_filename: string
  source_type: string
  provider: string
  llm_provider: string
  llm_model: string
  excel_mode: string
  error: string | null
  indexed_content: IndexedContent | null
  extraction_passes: ExtractionPass[]
  cics_result: CICSResult | null
  validation_result: ValidationResult | null
  created_at: string
  updated_at: string
}

export type StageStatus = 'pending' | 'active' | 'done' | 'failed'

export interface Stage {
  id: string
  label: string
  status: StageStatus
  available: boolean
}

// ── Composable ────────────────────────────────────────────────────────────────

export function useCommissionIntakeDetail() {
  const route = useRoute()
  const userStore = useUserStore()
  const { getBaseUrl } = useBaseUrl()

  const doc = ref<CommissionDocument | null>(null)
  const loadError = ref(false)
  const activeStage = ref<string>('indexed')
  const copied = ref(false)
  const activeCicsTab = ref<'summary' | 'products' | 'json'>('summary')
  const actionLoading = ref<string | null>(null)
  const actionError = ref<string | null>(null)
  const activePassSheetKey = ref<string>('')

  let pollTimer: ReturnType<typeof setInterval> | null = null

  // ── Stage helpers ──────────────────────────────────────────────────────────

  function passesForNum(passNum: number): ExtractionPass[] {
    return (doc.value?.extraction_passes || []).filter(p => p.pass_number === passNum)
  }

  function passAggregateStatus(passNum: number): StageStatus {
    const passes = passesForNum(passNum)
    if (!passes.length) return 'pending'
    if (passes.every(p => p.status === 'completed')) return 'done'
    if (passes.some(p => p.status === 'running')) return 'active'
    if (passes.some(p => p.status === 'failed') && passes.every(p => p.status === 'failed' || p.status === 'completed')) return 'failed'
    if (doc.value?.status === 'completed' && passes.some(p => p.status === 'completed')) return 'done'
    return 'active'
  }

  const stages = computed<Stage[]>(() => {
    if (!doc.value) return []
    const d = doc.value
    const status = d.status
    const failed = status === 'failed'

    function stageStatus(hasData: boolean, isCurrentStage: boolean): StageStatus {
      if (hasData) return 'done'
      if (isCurrentStage && !failed) return 'active'
      if (failed && !hasData) return 'failed'
      return 'pending'
    }

    const hasIndexed = !!d.indexed_content
    const hasAnyPass1 = passesForNum(1).length > 0
    const hasAnyPass2 = passesForNum(2).length > 0
    const hasAnyPass3 = passesForNum(3).length > 0
    const hasCics = !!d.cics_result
    const hasValidation = !!d.validation_result

    const indexingActive = status === 'indexing' || status === 'initiated'
    const extractingActive = status === 'extracting'

    const pass1Status: StageStatus = hasAnyPass1 ? passAggregateStatus(1) : stageStatus(false, extractingActive)
    const pass2Status: StageStatus = hasAnyPass2 ? passAggregateStatus(2) : stageStatus(false, extractingActive && passAggregateStatus(1) === 'done')
    const pass3Status: StageStatus = hasAnyPass3 ? passAggregateStatus(3) : stageStatus(false, extractingActive && passAggregateStatus(2) === 'done')

    return [
      { id: 'indexed', label: 'Indexed', status: stageStatus(hasIndexed, indexingActive), available: hasIndexed },
      { id: 'pass1', label: 'Pass 1', status: pass1Status, available: hasAnyPass1 },
      { id: 'pass2', label: 'Pass 2', status: pass2Status, available: hasAnyPass2 },
      { id: 'pass3', label: 'Pass 3', status: pass3Status, available: hasAnyPass3 },
      { id: 'cics', label: 'CICS Result', status: stageStatus(hasCics, extractingActive && pass3Status === 'done' && !hasCics), available: hasCics },
      { id: 'validation', label: 'Validation', status: stageStatus(hasValidation, status === 'completed' && !hasValidation), available: hasValidation },
    ]
  })

  watch(stages, (newStages) => {
    const available = newStages.filter(s => s.available)
    if (available.length && !available.find(s => s.id === activeStage.value)) {
      activeStage.value = available[available.length - 1]!.id
    }
  }, { immediate: true })

  // ── Per-sheet pass data ────────────────────────────────────────────────────

  const currentPassSheets = computed<{ key: string; label: string; attachment: string; sheet: string }[]>(() => {
    const num = activeStage.value === 'pass1' ? 1 : activeStage.value === 'pass2' ? 2 : activeStage.value === 'pass3' ? 3 : null
    if (num === null) return []
    const passes = passesForNum(num)
    const seen = new Set<string>()
    const result: { key: string; label: string; attachment: string; sheet: string }[] = []
    const distinctAttachments = new Set(passes.map(p => p.attachment_name).filter(Boolean))
    const multiAttachment = distinctAttachments.size > 1
    for (const p of passes) {
      const key = `${p.attachment_name}||${p.sheet_name}`
      if (!seen.has(key)) {
        seen.add(key)
        const sheetLabel = p.sheet_name || 'Document'
        const label = (multiAttachment && p.attachment_name) ? `${p.attachment_name} / ${sheetLabel}` : sheetLabel
        result.push({ key, label, attachment: p.attachment_name, sheet: p.sheet_name })
      }
    }
    return result
  })

  watch([activeStage, currentPassSheets], ([stage, sheets]) => {
    if (['pass1', 'pass2', 'pass3'].includes(stage) && sheets.length && !sheets.find(s => s.key === activePassSheetKey.value)) {
      activePassSheetKey.value = sheets[0]!.key
    }
  }, { immediate: true })

  const currentPass = computed<ExtractionPass | null>(() => {
    const num = activeStage.value === 'pass1' ? 1 : activeStage.value === 'pass2' ? 2 : activeStage.value === 'pass3' ? 3 : null
    if (num === null || !doc.value) return null
    const [attName, sheetName] = activePassSheetKey.value.split('||')
    return doc.value.extraction_passes.find(
      p => p.pass_number === num && p.attachment_name === (attName ?? '') && p.sheet_name === (sheetName ?? '')
    ) || null
  })

  // ── CICS computed ──────────────────────────────────────────────────────────

  const cicsDoc = computed(() => doc.value?.cics_result?.cics_doc as Record<string, unknown> | undefined)

  const cicsMetrics = computed(() => {
    const d = cicsDoc.value
    if (!d) return []
    const products = (d.products as unknown[]) || []
    const ruleCount = products.reduce((sum, p: any) => sum + (p.commission_rules?.length || 0), 0)
    return [
      { label: 'Products', value: products.length },
      { label: 'Total Rules', value: ruleCount },
      { label: 'Duration', value: doc.value?.cics_result?.duration_seconds ? `${doc.value.cics_result.duration_seconds.toFixed(1)}s` : '—' },
      { label: 'Schema', value: (d.schema_version as string) || '—' },
    ]
  })

  const cicsMetadataRows = computed(() => {
    const d = cicsDoc.value
    if (!d) return []
    const meta = d.document_metadata as Record<string, unknown> | undefined
    const provider = d.provider as Record<string, unknown> | undefined
    const period = d.commission_period as Record<string, unknown> | undefined
    return [
      { key: 'document_id', value: meta?.document_id },
      { key: 'provider', value: (provider as any)?.name },
      { key: 'period_label', value: (period as any)?.label },
      { key: 'effective_from', value: (period as any)?.effective_from },
      { key: 'effective_to', value: (period as any)?.effective_to },
      { key: 'source_file', value: meta?.source_file },
    ]
  })

  const cicsProducts = computed(() => {
    const d = cicsDoc.value
    if (!d) return []
    return (d.products as any[]) || []
  })

  // ── Validation computed ────────────────────────────────────────────────────

  const validationBannerClass = computed(() => {
    const vr = doc.value?.validation_result
    if (!vr) return ''
    if (vr.has_critical || vr.has_high) return 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 border border-red-200 dark:border-red-800'
    if (!vr.is_valid) return 'bg-yellow-50 dark:bg-yellow-900/20 text-yellow-700 dark:text-yellow-400 border border-yellow-200 dark:border-yellow-800'
    return 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400 border border-green-200 dark:border-green-800'
  })

  const validationBannerIcon = computed(() => {
    const vr = doc.value?.validation_result
    if (!vr) return 'i-lucide-circle'
    if (vr.has_critical || vr.has_high) return 'i-lucide-x-circle'
    if (!vr.is_valid) return 'i-lucide-alert-triangle'
    return 'i-lucide-check-circle-2'
  })

  const validationBannerText = computed(() => {
    const vr = doc.value?.validation_result
    if (!vr) return ''
    if (vr.has_critical) return 'Validation failed — critical issues found'
    if (vr.has_high) return 'Validation failed — high priority issues found'
    if (!vr.is_valid) return 'Validation passed with warnings'
    return 'Validation passed'
  })

  // ── Helpers ────────────────────────────────────────────────────────────────

  function isInFlight(status: string): boolean {
    return ['initiated', 'indexing', 'extracting'].includes(status)
  }

  function getStatusBadgeClass(status: string): string {
    switch (status) {
      case 'initiated': return 'bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-400'
      case 'indexing': return 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400'
      case 'extracting': return 'bg-indigo-100 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-400'
      case 'completed': return 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'
      case 'failed': return 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400'
      default: return 'bg-gray-100 dark:bg-gray-900/30 text-gray-700 dark:text-gray-400'
    }
  }

  function formatStatus(status: string): string {
    return status.charAt(0).toUpperCase() + status.slice(1)
  }

  function getPassStatusBadge(status: string): string {
    switch (status) {
      case 'completed': return 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'
      case 'running': return 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400'
      case 'failed': return 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400'
      default: return 'bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400'
    }
  }

  function getSeverityBadge(severity: string): string {
    switch (severity?.toUpperCase()) {
      case 'CRITICAL': return 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400'
      case 'HIGH': return 'bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-400'
      case 'MEDIUM': return 'bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-400'
      default: return 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400'
    }
  }

  function getSheetClassification(sheetName: string, indexed: IndexedContent): string {
    const classifications = (indexed.sheet_metadata as any)?.classifications || {}
    const entry = classifications[sheetName]
    return entry?.classification || entry?.sheet_type || 'unknown'
  }

  function getSheetClassBadge(sheetName: string, indexed: IndexedContent): string {
    const cls = getSheetClassification(sheetName, indexed)
    if (cls === 'commission_data') return 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'
    if (cls === 'master_data') return 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400'
    return 'bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400'
  }

  function formatCommissionValue(outcome: any): string {
    if (!outcome) return '—'
    if (outcome.percentage_value !== null && outcome.percentage_value !== undefined) return `${outcome.percentage_value}%`
    if (outcome.flat_value !== null && outcome.flat_value !== undefined) return `${outcome.flat_currency || 'INR'} ${outcome.flat_value}`
    if (outcome.tiers?.length) return `Tiered (${outcome.tiers.length} tiers)`
    return '—'
  }

  function formatConditions(conditions: any): string {
    if (!conditions) return 'All'
    const labelMap: Record<string, string> = {
      vehicle_sub_class: 'vehicle', policy_type: 'policy', state: 'state',
      booking_mode: 'booking', business_type: 'business', fuel_type: 'fuel',
      number_of_wheels: 'wheels', vehicle_carrier: 'carrier', school_bus_type: 'school bus',
      vehicle_make: 'make', vehicle_model: 'model', rto: 'RTO',
      zero_dep: 'zero dep', ncb: 'NCB', cpa: 'CPA', vehicle_age: 'vehicle age',
      gvw_start_range: 'GVW from', gvw_end_range: 'GVW to',
      cc_start_range: 'CC from', cc_end_range: 'CC to',
      seating_capacity_start_range: 'seating from', seating_capacity_end_range: 'seating to',
      od_term: 'OD term', tp_term: 'TP term', deal_parameter: 'deal param',
    }
    const parts: string[] = []
    for (const [key, value] of Object.entries(conditions)) {
      if (value === null || value === undefined) continue
      const label = labelMap[key] || key.replace(/_/g, ' ')
      if (Array.isArray(value) && value.length) parts.push(`${label}: ${value.join(', ')}`)
      else if (typeof value === 'boolean') parts.push(`${label}: ${value ? 'yes' : 'no'}`)
      else if (typeof value === 'number') parts.push(`${label}: ${value}`)
      else if (typeof value === 'string' && value) parts.push(`${label}: ${value}`)
    }
    return parts.join(' · ') || 'All'
  }

  function sheetPassStatus(passNum: number, attachment: string, sheet: string): string {
    return doc.value?.extraction_passes.find(
      (p: ExtractionPass) => p.pass_number === passNum && p.attachment_name === attachment && p.sheet_name === sheet
    )?.status ?? ''
  }

  async function copyJson(data: unknown) {
    await navigator.clipboard.writeText(JSON.stringify(data, null, 2))
    copied.value = true
    setTimeout(() => { copied.value = false }, 2000)
  }

  function downloadJson() {
    if (!doc.value?.cics_result) return
    const blob = new Blob([JSON.stringify(doc.value.cics_result.cics_doc, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${doc.value.original_filename.replace(/\.[^.]+$/, '')}_cics.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  // ── Data fetching + polling ────────────────────────────────────────────────

  async function getAuthHeaders() {
    const token = await userStore.getToken()
    return { Authorization: `Bearer ${token}` }
  }

  async function loadDoc() {
    try {
      const headers = await getAuthHeaders()
      doc.value = await $fetch<CommissionDocument>(
        `${getBaseUrl()}/api/commission-intake/documents/${route.params.id}/`,
        { headers }
      )
    } catch (err) {
      console.error('Failed to load document:', err)
      loadError.value = true
    }
  }

  async function pollDoc() {
    if (!doc.value || !isInFlight(doc.value.status)) return
    try {
      const headers = await getAuthHeaders()
      doc.value = await $fetch<CommissionDocument>(
        `${getBaseUrl()}/api/commission-intake/documents/${route.params.id}/`,
        { headers }
      )
    } catch (err) {
      console.error('Polling error:', err)
    }
  }

  async function triggerIndex() {
    actionLoading.value = 'index'
    actionError.value = null
    try {
      const headers = await getAuthHeaders()
      doc.value = await $fetch<CommissionDocument>(
        `${getBaseUrl()}/api/commission-intake/documents/${route.params.id}/index/`,
        { method: 'POST', headers }
      )
    } catch (err: any) {
      actionError.value = err?.data?.detail || 'Failed to trigger indexing'
    } finally {
      actionLoading.value = null
    }
  }

  async function triggerExtract() {
    actionLoading.value = 'extract'
    actionError.value = null
    try {
      const headers = await getAuthHeaders()
      doc.value = await $fetch<CommissionDocument>(
        `${getBaseUrl()}/api/commission-intake/documents/${route.params.id}/extract/`,
        { method: 'POST', headers }
      )
    } catch (err: any) {
      actionError.value = err?.data?.detail || 'Failed to trigger extraction'
    } finally {
      actionLoading.value = null
    }
  }

  async function triggerRerunPass(passNumber: number, sheetName: string, attachmentName: string) {
    actionLoading.value = `rerun-pass-${passNumber}`
    actionError.value = null
    try {
      const headers = await getAuthHeaders()
      await $fetch(
        `${getBaseUrl()}/api/commission-intake/documents/${route.params.id}/rerun-pass/`,
        { method: 'POST', headers, body: { pass_number: passNumber, sheet_name: sheetName, attachment_name: attachmentName } }
      )
      await loadDoc()
    } catch (err: any) {
      actionError.value = err?.data?.detail || `Failed to rerun pass ${passNumber}`
    } finally {
      actionLoading.value = null
    }
  }

  async function triggerTransform() {
    actionLoading.value = 'transform'
    actionError.value = null
    try {
      const headers = await getAuthHeaders()
      await $fetch(
        `${getBaseUrl()}/api/commission-intake/documents/${route.params.id}/transform/`,
        { method: 'POST', headers }
      )
      await loadDoc()
    } catch (err: any) {
      actionError.value = err?.data?.detail || 'Failed to trigger transform'
    } finally {
      actionLoading.value = null
    }
  }

  async function triggerValidate() {
    actionLoading.value = 'validate'
    actionError.value = null
    try {
      const headers = await getAuthHeaders()
      await $fetch(
        `${getBaseUrl()}/api/commission-intake/documents/${route.params.id}/validate/`,
        { method: 'POST', headers }
      )
      await loadDoc()
    } catch (err: any) {
      actionError.value = err?.data?.detail || 'Failed to trigger validation'
    } finally {
      actionLoading.value = null
    }
  }

  onMounted(() => {
    loadDoc()
    pollTimer = setInterval(pollDoc, 3000)
  })

  onUnmounted(() => {
    if (pollTimer) clearInterval(pollTimer)
  })

  return {
    doc, loadError, activeStage, copied, activeCicsTab, actionLoading, actionError, activePassSheetKey,
    stages, currentPassSheets, currentPass,
    cicsDoc, cicsMetrics, cicsMetadataRows, cicsProducts,
    validationBannerClass, validationBannerIcon, validationBannerText,
    passesForNum, passAggregateStatus,
    isInFlight, getStatusBadgeClass, formatStatus, getPassStatusBadge, getSeverityBadge,
    getSheetClassification, getSheetClassBadge, formatCommissionValue, formatConditions,
    sheetPassStatus, copyJson, downloadJson, loadDoc,
    triggerIndex, triggerExtract, triggerRerunPass, triggerTransform, triggerValidate,
  }
}
