import { ref, computed, onMounted, watch } from 'vue'
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
  overlapping_dimensions?: string[]
  suggestion?: string
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
  /** Deliberate normalizations, not problems. Optional: rows written before the
   *  field was added have no value. */
  notices?: string[]
  errors: string[]
  duration_seconds: number
  sheet_results: unknown[]
}

/** Commission outcome as it appears in the CICS document. */
export interface RuleOutcome {
  commission_type?: string
  percentage_value?: number | null
  flat_value?: number | null
  flat_currency?: string | null
  tiers?: unknown[]
}

export interface CicsProduct {
  product_name?: string
  sub_category?: string
  commission_rules?: { rule_id: string; outcome?: RuleOutcome }[]
}

/** Message from a failed $fetch, without reaching for `any`. */
function errorDetail(err: unknown): string {
  const detail = (err as { data?: { detail?: unknown } } | null)?.data?.detail
  return typeof detail === 'string' ? detail : ''
}

export interface IndexedContent {
  source_type: string
  primary_content: string
  email_context: string
  sheet_contents: Record<string, string>
  sheet_metadata: Record<string, unknown>
  indexer_version: string
  /** Backend's live CINS_SKIP_HIDDEN_SHEETS flag. Absent on older API responses. */
  hidden_sheets_excluded?: boolean
}

export interface SheetCoverage {
  /** Every sheet the indexer captured, hidden ones included. */
  total: number
  /** Sheets that fed extraction (total minus hidden minus reference-only). */
  extracted: number
  /** Sheets excluded because their Excel tab is hidden/veryHidden. */
  hiddenNames: string[]
  /** Visible sheets routing left out: master data, plus unclassified sheets
   *  that are skipped whenever commission sheets exist. */
  referenceCount: number
  allHidden: boolean
}

// ── Sheet classification + visibility ────────────────────────────────────────
// Visibility is deliberately kept orthogonal to classification: folding a
// HIDDEN value into getSheetClassification would erase the "hidden AND
// commission_data" case, which is the signal that the wrong tab was hidden.
//
// These are module-level pure functions so they can be tested without mounting
// the composable (see useCommissionIntakeDetail.test.ts).

type ClassificationEntry = {
  sheet_type?: string
  classification?: string
  visibility?: string
}

function classificationsOf(indexed: IndexedContent): Record<string, ClassificationEntry> {
  return (indexed.sheet_metadata?.classifications ?? {}) as Record<string, ClassificationEntry>
}

export function getSheetClassification(sheetName: string, indexed: IndexedContent): string {
  const entry = classificationsOf(indexed)[sheetName]
  return entry?.classification || entry?.sheet_type || 'unknown'
}

export function getSheetClassBadge(sheetName: string, indexed: IndexedContent): string {
  const cls = getSheetClassification(sheetName, indexed)
  if (cls === 'commission_data') return 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'
  if (cls === 'master_data') return 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400'
  return 'bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400'
}

const VISIBILITY_INDEXER_VERSION = [1, 4]

/**
 * Whether this document was indexed by a version that records tab visibility.
 * An absent visibility key is not evidence that a sheet was visible — for
 * pre-1.4 rows we simply do not know, so we say nothing at all.
 *
 * Compared component-wise, not with parseFloat: parseFloat('1.10') is 1.1,
 * which is < 1.4, so a float compare would silently hide the whole coverage
 * surface the day INDEXER_VERSION reaches 1.10.
 */
export function supportsVisibility(indexed: IndexedContent): boolean {
  const parts = String(indexed.indexer_version || '').split('.').map(n => parseInt(n, 10))
  if (!parts.length || parts.some(n => !Number.isFinite(n))) return false
  for (let i = 0; i < VISIBILITY_INDEXER_VERSION.length; i++) {
    const actual = parts[i] ?? 0
    const required = VISIBILITY_INDEXER_VERSION[i]!
    if (actual !== required) return actual > required
  }
  return true
}

/** Recorded Excel tab state. Absent key => 'visible' (matches the backend). */
export function getSheetVisibility(sheetName: string, indexed: IndexedContent): string {
  return classificationsOf(indexed)[sheetName]?.visibility || 'visible'
}

export function isSheetHidden(sheetName: string, indexed: IndexedContent): boolean {
  // `hidden_sheets_excluded` is the backend's live rollout flag. Absent (older
  // API) defaults to true, matching the shipped default. Without it, turning
  // CINS_SKIP_HIDDEN_SHEETS off would make the backend extract hidden sheets
  // while this UI kept reporting them as skipped.
  if (indexed.hidden_sheets_excluded === false) return false
  return supportsVisibility(indexed) && getSheetVisibility(sheetName, indexed) !== 'visible'
}

/**
 * Mirror of the backend's `derive_pipeline_routing`: when any visible sheet is
 * classified commission_data, ONLY those sheets are extracted; otherwise every
 * visible sheet is. Counting "extracted" as "not hidden and not master" would
 * over-report a visible unclassified sheet as extracted (routing skips it when
 * commission sheets exist) and under-report master sheets in the fallback case.
 */
function routedSheetNames(names: string[], indexed: IndexedContent): string[] {
  const visible = names.filter(n => !isSheetHidden(n, indexed))
  // `sheet_type` only, matching the backend's `sheet_type_of`. This branch must
  // NOT use getSheetClassification, whose `classification` fallback the backend
  // deliberately dropped — honouring a key the router ignores would make the
  // coverage numbers disagree with what actually got extracted.
  const commission = visible.filter(
    n => classificationsOf(indexed)[n]?.sheet_type === 'commission_data'
  )
  return commission.length ? commission : visible
}

/**
 * Split the indexed sheets into the ones that fed extraction and the ones whose
 * Excel tab is hidden. The two are listed separately in the UI: a hidden tab is
 * almost always hidden on purpose, so interleaving them with the real sheets
 * makes a 14-sheet workbook read as if 10 things went wrong.
 */
export function partitionSheetNames(
  indexed: IndexedContent | undefined | null
): { visible: string[]; hidden: string[] } {
  if (!indexed) return { visible: [], hidden: [] }
  const names = Object.keys(indexed.sheet_contents || {})
  return {
    visible: names.filter(n => !isSheetHidden(n, indexed)),
    hidden: names.filter(n => isSheetHidden(n, indexed)),
  }
}

/**
 * Human labels for RuleConditions keys. Anything absent falls through to the
 * underscore-replaced key, which reads noticeably worse next to its labelled
 * siblings ("policies count start range" vs "CC from") — so keep new condition
 * fields in here when they are added on the backend.
 */
const CONDITION_LABELS: Record<string, string> = {
  vehicle_sub_class: 'vehicle', policy_type: 'policy', state: 'state',
  booking_mode: 'booking', business_type: 'business', fuel_type: 'fuel',
  number_of_wheels: 'wheels', vehicle_carrier: 'carrier', school_bus_type: 'school bus',
  vehicle_make: 'make', vehicle_model: 'model', rto: 'RTO',
  zero_dep: 'zero dep', ncb: 'NCB', cpa: 'CPA', vehicle_age: 'vehicle age',
  gvw_start_range: 'GVW from', gvw_end_range: 'GVW to',
  cc_start_range: 'CC from', cc_end_range: 'CC to',
  ncb_start_range: 'NCB from', ncb_end_range: 'NCB to',
  seating_capacity_start_range: 'seating from', seating_capacity_end_range: 'seating to',
  policies_count_start_range: 'policies from', policies_count_end_range: 'policies to',
  vehicle_make_exclude: 'make excl', vehicle_model_exclude: 'model excl',
  od_term: 'OD term', tp_term: 'TP term', deal_parameter: 'deal param',
}

export function formatConditions(conditions: Record<string, unknown> | null | undefined): string {
  if (!conditions) return 'All'
  const parts: string[] = []
  for (const [key, value] of Object.entries(conditions)) {
    if (value === null || value === undefined) continue
    const label = CONDITION_LABELS[key] || key.replace(/_/g, ' ')
    if (Array.isArray(value) && value.length) parts.push(`${label}: ${value.join(', ')}`)
    else if (typeof value === 'boolean') parts.push(`${label}: ${value ? 'yes' : 'no'}`)
    else if (typeof value === 'number') parts.push(`${label}: ${value}`)
    else if (typeof value === 'string' && value) parts.push(`${label}: ${value}`)
  }
  return parts.join(' · ') || 'All'
}

export function computeSheetCoverage(
  indexed: IndexedContent | undefined | null
): SheetCoverage | null {
  if (!indexed || !supportsVisibility(indexed)) return null

  const names = Object.keys(indexed.sheet_contents || {})
  if (!names.length) return null

  const hiddenNames = names.filter(n => isSheetHidden(n, indexed))
  const routed = new Set(routedSheetNames(names, indexed))

  return {
    total: names.length,
    extracted: routed.size,
    hiddenNames,
    referenceCount: names.length - routed.size - hiddenNames.length,
    allHidden: hiddenNames.length === names.length,
  }
}

export function coverageText(coverage: SheetCoverage | null): string {
  if (!coverage) return ''
  if (coverage.allHidden) {
    return `Nothing to extract — all ${coverage.total} sheet(s) in this document are hidden.`
  }
  const parts: string[] = []
  const hidden = coverage.hiddenNames.length
  if (hidden) {
    parts.push(`${hidden} hidden ${hidden === 1 ? 'sheet' : 'sheets'} skipped`)
  }
  if (coverage.referenceCount) {
    // Not all of these are master data — an unclassified sheet is also left out
    // of routing when commission sheets exist. Say "not extracted", which is
    // true of both, rather than "reference only", which is true of only one.
    parts.push(`${coverage.referenceCount} not extracted`)
  }
  return `${coverage.extracted} of ${coverage.total} sheets extracted — ${parts.join(', ')}.`
}

export interface ExtractionPass {
  pass_number: 1 | 2 | 3
  sheet_name: string
  attachment_name: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  result: Record<string, unknown> | null
  prompt_text: string
  system_instruction: string
  llm_model_used: string
  extraction_method: 'llm' | 'codegen'
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
  is_validated: boolean
  validated_at: string | null
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
  const refreshing = ref(false)

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
    const ruleCount = products.reduce((sum, p) => sum + ((p as CicsProduct).commission_rules?.length || 0), 0)
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
    const changeRef = d.change_reference as Record<string, unknown> | undefined
    const rows = [
      { key: 'document_id', value: meta?.document_id },
      { key: 'provider', value: (provider as Record<string, string>)?.provider_name },
      { key: 'period_label', value: (period as Record<string, string>)?.period_label },
      { key: 'effective_from', value: (period as Record<string, string>)?.effective_from },
      { key: 'effective_to', value: (period as Record<string, string>)?.effective_to },
      { key: 'source_file', value: meta?.source_file },
    ]
    if (changeRef?.is_inherited) {
      rows.push({ key: 'inherited_from', value: changeRef.inheritance_notes as string || 'Rates continued from previous period' })
    }
    return rows
  })

  const cicsProducts = computed(() => {
    const d = cicsDoc.value
    if (!d) return []
    return (d.products as CicsProduct[]) || []
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

  const sheetCoverage = computed<SheetCoverage | null>(
    () => computeSheetCoverage(doc.value?.indexed_content)
  )

  /** Sheets that fed extraction vs sheets whose Excel tab is hidden. Listed in
   *  two places in the UI so the hidden ones never pad the main list. */
  const excelSheets = computed(() => partitionSheetNames(doc.value?.indexed_content))

  /** The coverage strip is a fact about the document, not about a stage — it
   *  renders as soon as the document is indexed, so it is a pre-flight check
   *  rather than a postmortem. Nothing to say when every sheet was extracted. */
  const showCoverageStrip = computed(() => {
    const c = sheetCoverage.value
    return !!c && (c.hiddenNames.length > 0 || c.referenceCount > 0)
  })

  const coverageStripText = computed(() => coverageText(sheetCoverage.value))

  function formatCommissionValue(outcome: RuleOutcome | null | undefined): string {
    if (!outcome) return '—'
    if (outcome.percentage_value !== null && outcome.percentage_value !== undefined) return `${outcome.percentage_value}%`
    if (outcome.flat_value !== null && outcome.flat_value !== undefined) return `${outcome.flat_currency || 'INR'} ${outcome.flat_value}`
    if (outcome.tiers?.length) return `Tiered (${outcome.tiers.length} tiers)`
    return '—'
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

  async function downloadExcel() {
    if (!doc.value?.cics_result) return
    const headers = await getAuthHeaders()
    const blob = await $fetch<Blob>(
      `${getBaseUrl()}/api/commission-intake/documents/${route.params.id}/export-excel/`,
      { method: 'GET', headers, responseType: 'blob' }
    )
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${doc.value.original_filename.replace(/\.[^.]+$/, '')}_cics.xlsx`
    a.click()
    URL.revokeObjectURL(url)
  }

  async function downloadOriginal() {
    if (!doc.value) return
    const headers = await getAuthHeaders()
    const { url } = await $fetch<{ url: string }>(
      `${getBaseUrl()}/api/commission-intake/documents/${route.params.id}/download/`,
      { method: 'GET', headers }
    )
    const a = document.createElement('a')
    a.href = url
    a.download = doc.value.original_filename
    a.target = '_blank'
    a.rel = 'noopener'
    a.click()
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

  async function refresh() {
    refreshing.value = true
    try {
      await loadDoc()
    } finally {
      refreshing.value = false
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
    } catch (err: unknown) {
      actionError.value = errorDetail(err) || 'Failed to trigger indexing'
    } finally {
      actionLoading.value = null
    }
  }

  // `forceRefresh` bypasses the CICS output cache: the LLM passes re-run even for
  // unchanged input, and a gate-passing result replaces the pinned one.
  async function triggerExtract(options: { forceRefresh?: boolean } = {}) {
    actionLoading.value = 'extract'
    actionError.value = null
    try {
      const headers = await getAuthHeaders()
      doc.value = await $fetch<CommissionDocument>(
        `${getBaseUrl()}/api/commission-intake/documents/${route.params.id}/extract/`,
        { method: 'POST', headers, body: { force_refresh: options.forceRefresh === true } }
      )
    } catch (err: unknown) {
      actionError.value = errorDetail(err) || 'Failed to trigger extraction'
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
    } catch (err: unknown) {
      actionError.value = errorDetail(err) || `Failed to rerun pass ${passNumber}`
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
    } catch (err: unknown) {
      actionError.value = errorDetail(err) || 'Failed to trigger transform'
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
    } catch (err: unknown) {
      actionError.value = errorDetail(err) || 'Failed to trigger validation'
    } finally {
      actionLoading.value = null
    }
  }

  async function triggerMarkValidated() {
    actionLoading.value = 'mark-validated'
    actionError.value = null
    try {
      const headers = await getAuthHeaders()
      doc.value = await $fetch<CommissionDocument>(
        `${getBaseUrl()}/api/commission-intake/documents/${route.params.id}/mark-validated/`,
        { method: 'POST', headers }
      )
    } catch (err: unknown) {
      actionError.value = errorDetail(err) || 'Failed to mark document as validated'
    } finally {
      actionLoading.value = null
    }
  }

  async function triggerMarkInvalidated() {
    actionLoading.value = 'mark-invalidated'
    actionError.value = null
    try {
      const headers = await getAuthHeaders()
      doc.value = await $fetch<CommissionDocument>(
        `${getBaseUrl()}/api/commission-intake/documents/${route.params.id}/mark-invalidated/`,
        { method: 'POST', headers }
      )
    } catch (err: unknown) {
      actionError.value = errorDetail(err) || 'Failed to remove validation'
    } finally {
      actionLoading.value = null
    }
  }

  onMounted(() => {
    loadDoc()
  })

  return {
    doc, loadError, activeStage, copied, activeCicsTab, actionLoading, actionError, activePassSheetKey,
    refreshing,
    stages, currentPassSheets, currentPass,
    cicsDoc, cicsMetrics, cicsMetadataRows, cicsProducts,
    validationBannerClass, validationBannerIcon, validationBannerText,
    passesForNum, passAggregateStatus,
    sheetCoverage, showCoverageStrip, coverageStripText, excelSheets,
    isInFlight, getStatusBadgeClass, formatStatus, getPassStatusBadge, getSeverityBadge,
    getSheetClassification, getSheetClassBadge,
    formatCommissionValue, formatConditions,
    sheetPassStatus, copyJson, downloadJson, downloadExcel, downloadOriginal, loadDoc, refresh,
    triggerIndex, triggerExtract, triggerRerunPass, triggerTransform, triggerValidate,
    triggerMarkValidated, triggerMarkInvalidated,
  }
}
