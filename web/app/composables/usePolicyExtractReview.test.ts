import { describe, expect, it } from 'vitest'

import {
  CONFIDENCE_THRESHOLD,
  confidenceTone,
  fieldGroup,
  fieldLabel,
  flaggedCount,
  formatFieldValue,
  groupRows,
  isFlagged,
  normalizeConfidence,
  reviewBanner,
  toExtractedRows,
} from './usePolicyExtractReview'

/**
 * View-model for the Policy Extract review queue.
 *
 * These are the pure decisions the slide-over leans on: how a raw confidence is
 * coerced onto the 0–1 scale, which band (green/amber/red) a score lands in,
 * when a field is "flagged" for a human, and what the review banner says for
 * each `review_status`. The page's chips, the "Needs review only" filter and
 * the Approve flow all key off these, so the boundaries are pinned here.
 */

describe('normalizeConfidence', () => {
  it('passes through values already on the 0–1 scale', () => {
    expect(normalizeConfidence(0)).toBe(0)
    expect(normalizeConfidence(0.42)).toBe(0.42)
    expect(normalizeConfidence(1)).toBe(1)
  })

  it('rescales a 0–100 percentage down to 0–1', () => {
    expect(normalizeConfidence(92)).toBeCloseTo(0.92)
    expect(normalizeConfidence(100)).toBe(1)
  })

  it('coerces numeric strings', () => {
    expect(normalizeConfidence('0.8')).toBeCloseTo(0.8)
    expect(normalizeConfidence('85')).toBeCloseTo(0.85)
  })

  it('maps NaN / non-numeric / nullish / negative to 0', () => {
    expect(normalizeConfidence(NaN)).toBe(0)
    expect(normalizeConfidence('abc')).toBe(0)
    expect(normalizeConfidence(null)).toBe(0)
    expect(normalizeConfidence(undefined)).toBe(0)
    expect(normalizeConfidence(-0.5)).toBe(0)
  })
})

describe('confidenceTone boundaries', () => {
  it('is red below the 0.7 threshold', () => {
    expect(confidenceTone(0)).toBe('red')
    expect(confidenceTone(0.69)).toBe('red')
  })

  it('is amber from 0.7 up to (but not including) 0.9', () => {
    expect(confidenceTone(0.7)).toBe('amber')
    expect(confidenceTone(0.89)).toBe('amber')
  })

  it('is green at 0.9 and above', () => {
    expect(confidenceTone(0.9)).toBe('green')
    expect(confidenceTone(1)).toBe('green')
  })

  it('pins the exported threshold', () => {
    expect(CONFIDENCE_THRESHOLD).toBe(0.7)
  })
})

describe('isFlagged', () => {
  it('flags a field explicitly marked for verification even at high confidence', () => {
    expect(isFlagged({ confidence_score: 0.99, do_verification: 'TRUE' })).toBe(true)
  })

  it('flags a low-confidence field that was not marked for verification', () => {
    expect(isFlagged({ confidence_score: 0.4, do_verification: 'FALSE' })).toBe(true)
  })

  it('does not flag a clean, confident field', () => {
    expect(isFlagged({ confidence_score: 0.95, do_verification: 'FALSE' })).toBe(false)
  })

  it('treats a sub-threshold score coming in as a 0–100 percentage consistently', () => {
    // 65 rescales to 0.65, which is below 0.7 → flagged.
    expect(isFlagged({ confidence_score: 65, do_verification: 'FALSE' })).toBe(true)
  })

  it('is safe on null / non-object input', () => {
    expect(isFlagged(null)).toBe(false)
    expect(isFlagged(undefined)).toBe(false)
  })
})

describe('flaggedCount', () => {
  it('counts only the flagged fields', () => {
    const fields = {
      a: { confidence_score: 0.95, do_verification: 'FALSE' }, // clean
      b: { confidence_score: 0.4, do_verification: 'FALSE' }, // low conf
      c: { confidence_score: 0.99, do_verification: 'TRUE' }, // verify
      d: { confidence_score: 0.7, do_verification: 'FALSE' }, // exactly at threshold → clean
    }
    expect(flaggedCount(fields)).toBe(2)
  })

  it('is 0 for an empty or missing dict', () => {
    expect(flaggedCount({})).toBe(0)
    expect(flaggedCount(null)).toBe(0)
    expect(flaggedCount(undefined)).toBe(0)
  })
})

describe('reviewBanner', () => {
  it('describes an auto-verified request in green', () => {
    const banner = reviewBanner('auto_approved', 0)
    expect(banner.tone).toBe('green')
    expect(banner.icon).toBe('i-lucide-shield-check')
    expect(banner.text).toMatch(/auto-verified/i)
  })

  it('describes a needs-review request in amber with the flagged count', () => {
    const banner = reviewBanner('needs_review', 3)
    expect(banner.tone).toBe('amber')
    expect(banner.icon).toBe('i-lucide-alert-triangle')
    expect(banner.text).toContain('3 details')
  })

  it('singularises the flagged-detail copy', () => {
    expect(reviewBanner('needs_review', 1).text).toContain('1 detail flagged')
  })

  it('describes an approved request with the relative reviewed time', () => {
    const banner = reviewBanner('approved', 0, '2h ago')
    expect(banner.tone).toBe('blue')
    expect(banner.icon).toBe('i-lucide-check-circle')
    expect(banner.text).toBe('Reviewed & approved 2h ago')
  })

  it('approved without a timestamp reads plainly', () => {
    expect(reviewBanner('approved', 0).text).toBe('Reviewed & approved')
  })

  it('falls back to the auto-verified banner for a null status', () => {
    expect(reviewBanner(null, 0).tone).toBe('green')
  })
})

describe('fieldLabel', () => {
  it('maps the engineer field ids to business language', () => {
    expect(fieldLabel('cust_name')).toBe('Customer name')
    expect(fieldLabel('mobileNo')).toBe('Mobile number')
    expect(fieldLabel('sum_insured')).toBe('IDV (sum insured)')
    expect(fieldLabel('reg_num')).toBe('Registration number')
    expect(fieldLabel('stp_nstp')).toBe('STP / NSTP')
  })

  it('prettifies unmapped snake_case ids into sentence case', () => {
    expect(fieldLabel('some_unknown_field')).toBe('Some unknown field')
  })

  it('prettifies unmapped camelCase ids into sentence case', () => {
    expect(fieldLabel('someValue')).toBe('Some value')
  })
})

describe('toExtractedRows', () => {
  it('maps the full field object shape the table consumes', () => {
    const rows = toExtractedRows({
      policy_number: {
        value: 'PN-1',
        confidence_score: 0.95,
        is_extracted: 'TRUE',
        do_verification: 'FALSE',
      },
      cust_name: {
        value: 'Jane',
        confidence_score: 0.5,
        is_extracted: 'TRUE',
        do_verification: 'TRUE',
        edited: true,
      },
    })
    expect(rows).toHaveLength(2)
    const [pn, name] = rows
    expect(pn).toMatchObject({ field: 'policy_number', value: 'PN-1', confidence: 0.95, flagged: false, edited: false })
    expect(name).toMatchObject({ field: 'cust_name', value: 'Jane', doVerification: true, edited: true, flagged: true })
  })

  it('tolerates a bare (legacy) value with no confidence metadata', () => {
    const rows = toExtractedRows({ reg_num: 'KA01AB1234' } as never)
    expect(rows[0]).toMatchObject({
      field: 'reg_num',
      value: 'KA01AB1234',
      confidence: 0,
      flagged: false,
      isExtracted: true,
    })
  })

  it('returns an empty array for a missing dict', () => {
    expect(toExtractedRows(null)).toEqual([])
    expect(toExtractedRows(undefined)).toEqual([])
  })
})

describe('fieldGroup', () => {
  it('maps fields to their review section', () => {
    expect(fieldGroup('policy_number')).toBe('policy')
    expect(fieldGroup('cust_name')).toBe('customer')
    expect(fieldGroup('reg_num')).toBe('vehicle')
    expect(fieldGroup('premium')).toBe('premium')
    expect(fieldGroup('isZeroDepCover')).toBe('addons')
  })

  it('falls back to "other" for unmapped fields', () => {
    expect(fieldGroup('some_mystery_field')).toBe('other')
  })
})

describe('groupRows', () => {
  const row = (field: string) => ({
    field, value: 'x', confidence: 0.9, doVerification: false, isExtracted: true, edited: false, flagged: false,
  })

  it('buckets rows into the fixed section order, dropping empty groups', () => {
    const groups = groupRows([row('premium'), row('cust_name'), row('policy_number')])
    expect(groups.map(g => g.key)).toEqual(['policy', 'customer', 'premium'])
    expect(groups.map(g => g.label)).toEqual(['Policy', 'Customer', 'Premium & taxes'])
  })

  it('orders rows within a group by their canonical order, not input order', () => {
    const groups = groupRows([row('insurer_id'), row('policy_number')])
    expect(groups[0]!.rows.map(r => r.field)).toEqual(['policy_number', 'insurer_id'])
  })

  it('places unmapped fields in the Other section last', () => {
    const groups = groupRows([row('mystery'), row('policy_number')])
    expect(groups.map(g => g.key)).toEqual(['policy', 'other'])
  })
})

describe('formatFieldValue', () => {
  it('formats amount fields as Indian-grouped rupees', () => {
    expect(formatFieldValue('premium', '28470')).toBe('₹28,470')
    expect(formatFieldValue('sum_insured', '1185000')).toBe('₹11,85,000')
  })

  it('leaves a non-numeric amount untouched', () => {
    expect(formatFieldValue('premium', 'N/A')).toBe('N/A')
  })

  it('appends % to NCB when missing, and leaves it when present', () => {
    expect(formatFieldValue('ncb_percent', '25')).toBe('25%')
    expect(formatFieldValue('ncb_percent', '25%')).toBe('25%')
  })

  it('formats dd/mm/yyyy dates as "15 Mar 2026"', () => {
    expect(formatFieldValue('policy_start_date', '15/03/2026')).toBe('15 Mar 2026')
    expect(formatFieldValue('registration_date', '14-03-2022')).toBe('14 Mar 2022')
  })

  it('passes an unparsable date through unchanged', () => {
    expect(formatFieldValue('policy_start_date', 'TBD')).toBe('TBD')
  })

  it('renders an empty value as an em dash', () => {
    expect(formatFieldValue('cust_name', '')).toBe('—')
    expect(formatFieldValue('cust_name', null)).toBe('—')
  })

  it('leaves ordinary text fields alone', () => {
    expect(formatFieldValue('cust_name', 'ANANYA IYER')).toBe('ANANYA IYER')
  })
})
