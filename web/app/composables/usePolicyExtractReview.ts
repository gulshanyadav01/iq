/**
 * Pure view-model for the Policy Extract human-in-the-loop review queue.
 *
 * The backend returns each extracted field as
 * `{ value, confidence_score, is_extracted, do_verification, edited?, original_value? }`
 * where `confidence_score` is a 0–1 number and the two flags are the *strings*
 * `"TRUE"`/`"FALSE"`. A field is "flagged" (needs a human to look at it) when the
 * extractor asked for verification OR its confidence fell below the threshold.
 *
 * Everything here is a pure function so the confidence tones, the flag logic and
 * the review-banner copy can be unit-tested without mounting the page. The page
 * (`policy-extract.vue`) consumes `toExtractedRows()` for its table and the
 * helpers for the per-row chips and the review banner.
 */

/** Below this (on a 0–1 scale) a field is treated as low-confidence / flagged. */
export const CONFIDENCE_THRESHOLD = 0.7

/** A field passes the auto-approve confidence check at or above this. */
const HIGH_CONFIDENCE = 0.9

export type ConfidenceTone = 'green' | 'amber' | 'red'
export type ReviewStatus = 'auto_approved' | 'needs_review' | 'approved' | null

/** The raw shape of a single entry in `result.extracted_fields`. */
export interface ExtractedFieldData {
  value?: unknown
  confidence_score?: unknown
  is_extracted?: unknown
  do_verification?: unknown
  edited?: unknown
  original_value?: unknown
}

/** The row shape the slide-over's Extracted Fields table renders. */
export interface ExtractedRow {
  field: string
  value: unknown
  confidence: number
  doVerification: boolean
  isExtracted: boolean
  edited: boolean
  flagged: boolean
}

export interface ReviewBanner {
  tone: ConfidenceTone | 'blue'
  icon: string
  text: string
}

/**
 * Coerce a raw confidence into the 0–1 scale.
 * Accepts already-normalised 0–1 values, rescales anything > 1 (a 0–100
 * percentage), and maps NaN / non-numeric / null to 0 so an unscored field
 * sorts as lowest confidence rather than throwing off the chip colour.
 */
export function normalizeConfidence(raw: unknown): number {
  const n = typeof raw === 'number' ? raw : Number(raw)
  if (!Number.isFinite(n)) return 0
  if (n > 1) return n / 100
  if (n < 0) return 0
  return n
}

/** Chip colour band for a (already 0–1) confidence score. */
export function confidenceTone(score: number): ConfidenceTone {
  if (score >= HIGH_CONFIDENCE) return 'green'
  if (score >= CONFIDENCE_THRESHOLD) return 'amber'
  return 'red'
}

/** Tailwind classes (light + dark) for the confidence chip, keyed off the tone. */
export function confidenceClass(score: number): string {
  switch (confidenceTone(score)) {
    case 'green':
      return 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'
    case 'amber':
      return 'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400'
    default:
      return 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400'
  }
}

/** True when the extractor asked for verification or confidence is sub-threshold. */
export function isFlagged(fieldData: ExtractedFieldData | null | undefined): boolean {
  if (!fieldData || typeof fieldData !== 'object') return false
  if (fieldData.do_verification === 'TRUE') return true
  return normalizeConfidence(fieldData.confidence_score) < CONFIDENCE_THRESHOLD
}

/** How many fields in a `result.extracted_fields` dict are flagged. */
export function flaggedCount(
  extractedFields: Record<string, ExtractedFieldData> | null | undefined,
): number {
  if (!extractedFields || typeof extractedFields !== 'object') return 0
  return Object.values(extractedFields).filter(isFlagged).length
}

/**
 * Banner content for the completed-view header, driven by the request's
 * `review_status`. `reviewedAt` (an ISO string) is only used for the "approved"
 * copy and is rendered through the page's own `formatDate` relative formatter,
 * so it is passed in pre-formatted as `reviewedLabel`.
 */
export function reviewBanner(
  reviewStatus: ReviewStatus,
  flagged: number,
  reviewedLabel?: string | null,
): ReviewBanner {
  if (reviewStatus === 'approved') {
    return {
      tone: 'blue',
      icon: 'i-lucide-check-circle',
      text: reviewedLabel ? `Reviewed & approved ${reviewedLabel}` : 'Reviewed & approved',
    }
  }
  if (reviewStatus === 'needs_review') {
    return {
      tone: 'amber',
      icon: 'i-lucide-alert-triangle',
      text: `Needs review — ${flagged} detail${flagged === 1 ? '' : 's'} flagged for verification`,
    }
  }
  // auto_approved (and the null fallback) — all fields cleared the check.
  return {
    tone: 'green',
    icon: 'i-lucide-shield-check',
    text: 'Auto-verified — every detail cleared the confidence check',
  }
}

/**
 * Business-language labels for the extraction schema's raw field ids, so the
 * review pane never shows an engineer's `cust_name` / `mobileNo` to a reviewer.
 * Anything not mapped falls back to a prettified sentence-case of the id.
 */
export const FIELD_LABELS: Record<string, string> = {
  policy_number: 'Policy number',
  insurer_id: 'Insurer',
  plan_type_id: 'Plan type',
  policy_term: 'Policy term (years)',
  business_type: 'Business type',
  product_id: 'Product',
  cust_name: 'Customer name',
  mobileNo: 'Mobile number',
  email: 'Email',
  cust_address: 'Address',
  pinCode: 'PIN code',
  reg_num: 'Registration number',
  search_text_make: 'Make',
  model_name: 'Model',
  variantName: 'Variant',
  fuel_type: 'Fuel type',
  VehicleClass: 'Vehicle class',
  VehicleSubClass: 'Vehicle sub-class',
  engineNo: 'Engine number',
  chassisNo: 'Chassis number',
  manufacturing_date: 'Manufacturing date',
  registration_date: 'Registration date',
  cubic_capacity_value: 'Engine capacity (cc)',
  vehicle_weight_value: 'Gross vehicle weight (kg)',
  no_of_seats: 'Seats',
  no_of_wheels: 'Wheels',
  vechicle_carrier_id: 'Carrier / permit type',
  rto_num: 'RTO code',
  rto_city: 'RTO city',
  rto_state: 'RTO state',
  premium: 'Total premium',
  net_premium: 'Net premium',
  od_premium: 'Own-damage premium',
  tp_premium: 'Third-party premium',
  basic_od_premium: 'Basic OD premium',
  basic_tp_premium: 'Basic TP premium',
  sum_insured: 'IDV (sum insured)',
  ncb_percent: 'NCB %',
  cgst: 'CGST',
  sgst: 'SGST',
  igst: 'IGST',
  serviceTax: 'Total GST',
  finalTotalDiscount: 'Total discount',
  policy_start_date: 'Policy start date',
  policy_end_date: 'Policy end date',
  issuance_date: 'Issuance date',
  tpStartDate: 'TP start date',
  tpEndDate: 'TP end date',
  od_startdate: 'OD start date',
  od_enddate: 'OD end date',
  pay_term: 'Payment term',
  od_term: 'OD term (years)',
  tp_term: 'TP term (years)',
  previous_policy_number: 'Previous policy number',
  previous_policy_end_date: 'Previous policy end date',
  nomineeName: 'Nominee',
  nomineeRelationWithCustomer: 'Nominee relation',
  financialInstitutionName: 'Financier',
  intermediary_code: 'Intermediary code',
  intermediary_name: 'Intermediary',
  branch: 'Branch',
  AgentUserName: 'Agent',
  isZeroDepCover: 'Zero-dep cover',
  zeroDepPremium: 'Zero-dep premium',
  totalAddonPremium: 'Total add-on premium',
  stp_nstp: 'STP / NSTP',
  pa_cover: 'PA cover',
  pa_cover_addon: 'PA add-on',
}

/** Human label for a raw extraction field id (mapped, else prettified). */
export function fieldLabel(id: string): string {
  const mapped = FIELD_LABELS[id]
  if (mapped) return mapped
  const words = id
    .replace(/_/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .trim()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

// ─── Field grouping ─────────────────────────────────────────────────────────
// The schema has ~99 fields; a flat list reads like a database dump. Group them
// into the sections a human actually reviews, in a fixed reading order. Anything
// not listed falls into "Other".

export type FieldGroupKey = 'policy' | 'customer' | 'vehicle' | 'premium' | 'addons' | 'other'

/** Ordered group keys with their display labels (the render order of sections). */
export const FIELD_GROUPS: { key: FieldGroupKey; label: string }[] = [
  { key: 'policy', label: 'Policy' },
  { key: 'customer', label: 'Customer' },
  { key: 'vehicle', label: 'Vehicle' },
  { key: 'premium', label: 'Premium & taxes' },
  { key: 'addons', label: 'Add-on covers' },
  { key: 'other', label: 'Other' },
]

// field id → group. Order within each array is also the in-group render order.
const _GROUP_FIELDS: Record<Exclude<FieldGroupKey, 'other'>, string[]> = {
  policy: [
    'policy_number', 'insurer_id', 'plan_type_id', 'product_id', 'business_type',
    'policy_term', 'policy_start_date', 'policy_end_date', 'issuance_date',
    'od_startdate', 'od_enddate', 'tpStartDate', 'tpEndDate', 'od_term', 'tp_term',
    'pay_term', 'stp_nstp', 'previous_policy_number', 'previous_policy_end_date',
    'branch', 'intermediary_name', 'intermediary_code', 'AgentUserName',
    'financialInstitutionName',
  ],
  customer: [
    'cust_name', 'mobileNo', 'email', 'cust_address', 'pinCode',
    'nomineeName', 'nomineeRelationWithCustomer',
  ],
  vehicle: [
    'reg_num', 'search_text_make', 'model_name', 'variantName', 'fuel_type',
    'VehicleClass', 'VehicleSubClass', 'engineNo', 'chassisNo',
    'manufacturing_date', 'registration_date', 'cubic_capacity_value',
    'vehicle_weight_value', 'no_of_seats', 'no_of_wheels', 'vechicle_carrier_id',
    'rto_num', 'rto_city', 'rto_state',
  ],
  premium: [
    'premium', 'net_premium', 'od_premium', 'tp_premium', 'basic_od_premium',
    'basic_tp_premium', 'sum_insured', 'ncb_percent', 'finalTotalDiscount',
    'cgst', 'sgst', 'igst', 'serviceTax',
  ],
  addons: [
    'addOn', 'totalAddonPremium', 'isZeroDepCover', 'zeroDepPremium',
    'isEPCover', 'ePPremium', 'engineProtectionPremium', 'isConsumablesCover',
    'consumablesPremium', 'isKeyReplacementCover', 'keyReplacement', 'isNCBCover',
    'nCBProtection', 'isRSACover', 'rSAPremium', 'isRTICover', 'rtiPremium',
    'isLPBCover', 'lPBPremium', 'isTyreProtectionCover', 'tyreProtectionPremium',
    'isInsurerPaidDriver', 'paidDriverPremium', 'pa_cover', 'pa_cover_addon',
    'paUnnamedPassengerPremium', 'isCNGFitted', 'cngAmount', 'cngLPGUnitIDV',
    'bifuelKitTPPremium', 'electricalAccessoriesIDV', 'nonElectricalAccessoriesIDV',
    'valueOfElectricalAccessories', 'valueOfNonElectricalAccessories',
    'imtIncluded', 'IMT23_value', 'IMT34', 'IMT34_value', 'addonList',
  ],
}

// field id → canonical render index within its group (populated below).
const _FIELD_ORDER: Record<string, number> = {}
const _FIELD_TO_GROUP: Record<string, FieldGroupKey> = (() => {
  const map: Record<string, FieldGroupKey> = {}
  let i = 0
  for (const g of Object.keys(_GROUP_FIELDS) as Exclude<FieldGroupKey, 'other'>[]) {
    for (const f of _GROUP_FIELDS[g]) {
      map[f] = g
      _FIELD_ORDER[f] = i++
    }
  }
  return map
})()

/** Which group a field id belongs to (unmapped → 'other'). */
export function fieldGroup(id: string): FieldGroupKey {
  return _FIELD_TO_GROUP[id] ?? 'other'
}

/**
 * Bucket already-filtered rows into the fixed group order, each group's rows in
 * their canonical in-group order, dropping empty groups. The page renders one
 * collapsible section per returned group.
 */
export function groupRows(rows: ExtractedRow[]): { key: FieldGroupKey; label: string; rows: ExtractedRow[] }[] {
  const byGroup = new Map<FieldGroupKey, ExtractedRow[]>()
  for (const row of rows) {
    const g = fieldGroup(row.field)
    if (!byGroup.has(g)) byGroup.set(g, [])
    byGroup.get(g)!.push(row)
  }
  for (const list of byGroup.values()) {
    list.sort((a, b) => (_FIELD_ORDER[a.field] ?? 999) - (_FIELD_ORDER[b.field] ?? 999))
  }
  return FIELD_GROUPS
    .filter(g => byGroup.has(g.key))
    .map(g => ({ key: g.key, label: g.label, rows: byGroup.get(g.key)! }))
}

// ─── Value formatting (display mode only — the edit input keeps the raw value) ─
const _AMOUNT_FIELDS = new Set([
  'premium', 'net_premium', 'od_premium', 'tp_premium', 'basic_od_premium',
  'basic_tp_premium', 'sum_insured', 'finalTotalDiscount', 'cgst', 'sgst', 'igst',
  'serviceTax', 'totalAddonPremium', 'zeroDepPremium', 'ePPremium',
  'engineProtectionPremium', 'consumablesPremium', 'keyReplacement',
  'nCBProtection', 'rSAPremium', 'rtiPremium', 'lPBPremium', 'tyreProtectionPremium',
  'paidDriverPremium', 'pa_cover', 'paUnnamedPassengerPremium', 'cngAmount',
  'cngLPGUnitIDV', 'bifuelKitTPPremium', 'electricalAccessoriesIDV',
  'nonElectricalAccessoriesIDV', 'valueOfElectricalAccessories',
  'valueOfNonElectricalAccessories', 'IMT23_value', 'IMT34_value',
])
const _DATE_FIELDS = new Set([
  'policy_start_date', 'policy_end_date', 'issuance_date', 'tpStartDate',
  'tpEndDate', 'od_startdate', 'od_enddate', 'manufacturing_date',
  'registration_date', 'previous_policy_end_date',
])
const _MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const _INR = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 })

/** Parse a dd/mm/yyyy (or dd-mm-yyyy) string to "15 Mar 2026", else null. */
function _formatDmy(value: string): string | null {
  const m = value.trim().match(/^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$/)
  if (!m) return null
  const day = Number(m[1]); const mon = Number(m[2]); const year = m[3]
  if (mon < 1 || mon > 12 || day < 1 || day > 31) return null
  return `${day} ${_MONTHS[mon - 1]} ${year}`
}

/**
 * Human-friendly display of a captured value: ₹ for amount fields, % for NCB,
 * "15 Mar 2026" for dates. Anything it can't confidently format passes through
 * unchanged, and an empty value renders as an em dash.
 */
export function formatFieldValue(field: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  const str = String(value)
  if (field === 'ncb_percent') {
    return /%$/.test(str) ? str : `${str}%`
  }
  if (_AMOUNT_FIELDS.has(field)) {
    const n = Number(str.replace(/,/g, ''))
    if (Number.isFinite(n) && str.replace(/[,.\d]/g, '') === '') return `₹${_INR.format(n)}`
    return str
  }
  if (_DATE_FIELDS.has(field)) {
    return _formatDmy(str) ?? str
  }
  return str
}

/** Was this field extracted with a usable value? Mirrors the "Extracted Only" filter. */
function hasValue(value: unknown): boolean {
  return value !== null && value !== undefined && value !== '' && value !== '0' && value !== 'NO' && value !== 'FALSE'
}

/**
 * Map `result.extracted_fields` into the row shape the table consumes. Tolerates
 * the legacy shape where a field is a bare value rather than a `{ value, … }`
 * object (older results predate the confidence metadata) by treating the bare
 * value as the value with no confidence / flags.
 */
export function toExtractedRows(
  extractedFields: Record<string, ExtractedFieldData> | null | undefined,
): ExtractedRow[] {
  if (!extractedFields || typeof extractedFields !== 'object') return []
  return Object.entries(extractedFields).map(([field, data]) => {
    const isObject = typeof data === 'object' && data !== null
    const value = isObject && 'value' in data ? (data as ExtractedFieldData).value : data
    const confidence = normalizeConfidence(isObject ? (data as ExtractedFieldData).confidence_score : undefined)
    const doVerification = isObject && (data as ExtractedFieldData).do_verification === 'TRUE'
    const isExtracted = isObject
      ? (data as ExtractedFieldData).is_extracted === 'TRUE'
      : hasValue(value)
    const edited = isObject && (data as ExtractedFieldData).edited === true
    return {
      field,
      value,
      confidence,
      doVerification,
      isExtracted,
      edited,
      flagged: isObject ? isFlagged(data as ExtractedFieldData) : false,
    }
  })
}

/** The composable facade, mirroring `usePolicyExtractDownload`'s shape. */
export function usePolicyExtractReview() {
  return {
    CONFIDENCE_THRESHOLD,
    normalizeConfidence,
    confidenceTone,
    confidenceClass,
    isFlagged,
    flaggedCount,
    reviewBanner,
    toExtractedRows,
    fieldLabel,
    fieldGroup,
    groupRows,
    formatFieldValue,
    FIELD_GROUPS,
  }
}
