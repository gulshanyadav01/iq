import { describe, expect, it } from 'vitest'

import {
  computeSheetCoverage,
  coverageText,
  formatConditions,
  getSheetClassBadge,
  getSheetClassification,
  getSheetOverrideReason,
  getSheetVisibility,
  isSheetHidden,
  partitionSheetNames,
  supportsVisibility,
} from './useCommissionIntakeDetail'
import type { IndexedContent } from './useCommissionIntakeDetail'

/**
 * Sheet-coverage surface for hidden Excel worksheets.
 *
 * A hidden sheet is indexed but never extracted. The UI has to say so, and it
 * has to say so honestly in three directions:
 *   - never claim coverage it cannot verify (pre-1.4 documents),
 *   - never conflate "skipped" with "used as reference only",
 *   - never lose the hidden-AND-commission_data case, which is the signal that
 *     the workbook hid the wrong tab.
 */

function indexed(
  classifications: Record<string, Record<string, unknown>>,
  indexerVersion = '1.4'
): IndexedContent {
  return {
    source_type: 'excel',
    primary_content: '',
    email_context: '',
    sheet_contents: Object.fromEntries(Object.keys(classifications).map(n => [n, 'rows'])),
    sheet_metadata: { classifications },
    indexer_version: indexerVersion,
  }
}

const VISIBLE_GRID = { sheet_type: 'commission_data', visibility: 'visible' }
const HIDDEN_GRID = { sheet_type: 'commission_data', visibility: 'hidden' }
const VISIBLE_LEGEND = { sheet_type: 'master_data', visibility: 'visible' }
const VERY_HIDDEN_LEGEND = { sheet_type: 'master_data', visibility: 'veryHidden' }

describe('visibility helpers', () => {
  it('treats an absent visibility key as visible', () => {
    const doc = indexed({ Grid: { sheet_type: 'commission_data' } })
    expect(getSheetVisibility('Grid', doc)).toBe('visible')
    expect(isSheetHidden('Grid', doc)).toBe(false)
  })

  it('reports hidden and veryHidden tabs', () => {
    const doc = indexed({ A: HIDDEN_GRID, B: VERY_HIDDEN_LEGEND })
    expect(getSheetVisibility('A', doc)).toBe('hidden')
    expect(getSheetVisibility('B', doc)).toBe('veryHidden')
    expect(isSheetHidden('A', doc)).toBe(true)
    expect(isSheetHidden('B', doc)).toBe(true)
  })

  it('says nothing for documents indexed before visibility was recorded', () => {
    // Absent data is not evidence a sheet was visible — and it is certainly not
    // evidence it was hidden. Pre-1.4 rows must render no coverage claim at all.
    const legacy = indexed({ Grid: HIDDEN_GRID }, '1.3')
    expect(supportsVisibility(legacy)).toBe(false)
    expect(isSheetHidden('Grid', legacy)).toBe(false)
    expect(computeSheetCoverage(legacy)).toBeNull()
  })

  it('compares indexer_version component-wise, not as a float', () => {
    // parseFloat('1.10') === 1.1 < 1.4, so a float compare would silently hide
    // the entire coverage surface the day INDEXER_VERSION reaches 1.10.
    expect(supportsVisibility(indexed({}, '1.10'))).toBe(true)
    expect(supportsVisibility(indexed({}, '1.4'))).toBe(true)
    expect(supportsVisibility(indexed({}, '2.0'))).toBe(true)
    expect(supportsVisibility(indexed({}, '1.3'))).toBe(false)
    expect(supportsVisibility(indexed({}, '0.9'))).toBe(false)
  })

  it('treats a missing or malformed version as unknown, never as supported', () => {
    for (const version of ['', 'abc', 'v1.4', '1.x']) {
      expect(supportsVisibility(indexed({}, version))).toBe(false)
    }
  })
})

describe('routing parity with the backend', () => {
  // computeSheetCoverage must mirror derive_pipeline_routing: when any visible
  // commission sheet exists, ONLY those are extracted.

  it('does not count a visible unclassified sheet as extracted', () => {
    const coverage = computeSheetCoverage(
      indexed({
        Grid: VISIBLE_GRID,
        Notes: { sheet_type: null, visibility: 'visible' },
        Legend: VISIBLE_LEGEND,
      })
    )!
    expect(coverage.extracted).toBe(1)
    expect(coverage.referenceCount).toBe(2)
    expect(coverageText(coverage)).toContain('1 of 3 sheets extracted')
  })

  it('counts master sheets as extracted when there is no commission sheet', () => {
    // Routing falls back to every visible sheet, so the legend really is
    // extracted. Calling it "reference only" would under-report coverage.
    const coverage = computeSheetCoverage(
      indexed({ Legend: VISIBLE_LEGEND, 'Old Grid': HIDDEN_GRID })
    )!
    expect(coverage.extracted).toBe(1)
    expect(coverage.referenceCount).toBe(0)
    expect(coverage.hiddenNames).toEqual(['Old Grid'])
  })

  it('keeps the three buckets summing to the total', () => {
    for (const fixture of [
      { Grid: VISIBLE_GRID, Legend: VISIBLE_LEGEND, Old: HIDDEN_GRID },
      { Grid: VISIBLE_GRID, Notes: { sheet_type: null, visibility: 'visible' } },
      { A: HIDDEN_GRID, B: VERY_HIDDEN_LEGEND },
    ]) {
      const c = computeSheetCoverage(indexed(fixture))!
      expect(c.extracted + c.referenceCount + c.hiddenNames.length).toBe(c.total)
    }
  })

  it('keeps visibility orthogonal to classification', () => {
    // Folding HIDDEN into the classification badge would destroy the
    // "hidden sheet that looks like a commission grid" signal.
    const doc = indexed({ Grid: HIDDEN_GRID })
    expect(getSheetClassification('Grid', doc)).toBe('commission_data')
    expect(isSheetHidden('Grid', doc)).toBe(true)
  })
})

describe('computeSheetCoverage', () => {
  it('counts extracted, reference-only and skipped separately', () => {
    const coverage = computeSheetCoverage(
      indexed({
        Grid: VISIBLE_GRID,
        'Grid 2': VISIBLE_GRID,
        Legend: VISIBLE_LEGEND,
        'Old Grid': HIDDEN_GRID,
      })
    )!
    expect(coverage.total).toBe(4)
    expect(coverage.extracted).toBe(2)
    expect(coverage.referenceCount).toBe(1)
    expect(coverage.hiddenNames).toEqual(['Old Grid'])
    expect(coverage.allHidden).toBe(false)
  })

  it('never double-counts a sheet that is both hidden and master data', () => {
    const coverage = computeSheetCoverage(
      indexed({ Grid: VISIBLE_GRID, Legend: VERY_HIDDEN_LEGEND })
    )!
    expect(coverage.hiddenNames).toEqual(['Legend'])
    expect(coverage.referenceCount).toBe(0)
    expect(coverage.extracted).toBe(1)
    expect(coverage.extracted + coverage.referenceCount + coverage.hiddenNames.length)
      .toBe(coverage.total)
  })

  it('detects the all-hidden document', () => {
    const coverage = computeSheetCoverage(
      indexed({ Grid: HIDDEN_GRID, Legend: VERY_HIDDEN_LEGEND })
    )!
    expect(coverage.allHidden).toBe(true)
    expect(coverage.extracted).toBe(0)
  })

  it('returns null when there are no sheets at all', () => {
    expect(computeSheetCoverage(undefined)).toBeNull()
    expect(computeSheetCoverage(indexed({}))).toBeNull()
  })

  it('counts prefixed multi-attachment email sheets as distinct', () => {
    const coverage = computeSheetCoverage(
      indexed({ 'book_a__Grid': VISIBLE_GRID, 'book_b__Grid': HIDDEN_GRID })
    )!
    expect(coverage.total).toBe(2)
    expect(coverage.hiddenNames).toEqual(['book_b__Grid'])
  })
})

describe('rollout flag parity with the backend', () => {
  // CINS_SKIP_HIDDEN_SHEETS=false is the documented rollback: the backend goes
  // back to routing hidden sheets. If the UI kept its own opinion it would
  // report sheets as skipped that were actually extracted.

  function withFlag(value: boolean | undefined) {
    const doc = indexed({ Grid: VISIBLE_GRID, 'Old Grid': HIDDEN_GRID })
    if (value !== undefined) doc.hidden_sheets_excluded = value
    return doc
  }

  it('reports nothing as hidden when the backend flag is off', () => {
    const doc = withFlag(false)
    expect(isSheetHidden('Old Grid', doc)).toBe(false)
    expect(computeSheetCoverage(doc)!.hiddenNames).toEqual([])
    expect(partitionSheetNames(doc).hidden).toEqual([])
  })

  it('excludes hidden sheets when the flag is on', () => {
    expect(isSheetHidden('Old Grid', withFlag(true))).toBe(true)
  })

  it('defaults to excluding when the API omits the flag', () => {
    // Older API responses have no such field; the shipped default is on.
    expect(isSheetHidden('Old Grid', withFlag(undefined))).toBe(true)
  })
})

describe('partitionSheetNames', () => {
  // Hidden sheets are listed separately in the UI. A hidden tab is normally
  // hidden on purpose, so padding the main list with 10 of them reads as if 10
  // things went wrong.

  it('keeps hidden sheets out of the main list', () => {
    const doc = indexed({
      Grid: VISIBLE_GRID,
      'Old Grid': HIDDEN_GRID,
      Legend: VISIBLE_LEGEND,
      Archive: VERY_HIDDEN_LEGEND,
    })
    expect(partitionSheetNames(doc)).toEqual({
      visible: ['Grid', 'Legend'],
      hidden: ['Old Grid', 'Archive'],
    })
  })

  it('preserves workbook order within each group', () => {
    const doc = indexed({ A: HIDDEN_GRID, B: VISIBLE_GRID, C: HIDDEN_GRID, D: VISIBLE_GRID })
    const { visible, hidden } = partitionSheetNames(doc)
    expect(visible).toEqual(['B', 'D'])
    expect(hidden).toEqual(['A', 'C'])
  })

  it('leaves every sheet in the main list for a pre-1.4 document', () => {
    // Without recorded visibility we do not know, so nothing gets demoted.
    const legacy = indexed({ Grid: VISIBLE_GRID, 'Old Grid': HIDDEN_GRID }, '1.3')
    expect(partitionSheetNames(legacy)).toEqual({
      visible: ['Grid', 'Old Grid'],
      hidden: [],
    })
  })

  it('handles a missing document without throwing', () => {
    expect(partitionSheetNames(undefined)).toEqual({ visible: [], hidden: [] })
  })
})

describe('coverageText', () => {
  it('leads with the fraction, not a bare count', () => {
    const text = coverageText(
      computeSheetCoverage(
        indexed({ Grid: VISIBLE_GRID, Legend: VISIBLE_LEGEND, 'Old Grid': HIDDEN_GRID })
      )
    )
    expect(text).toContain('1 of 3 sheets extracted')
    expect(text).toContain('1 hidden sheet skipped')
    expect(text).toContain('1 not extracted')
  })

  it('pluralises the hidden-sheet count', () => {
    const text = coverageText(
      computeSheetCoverage(
        indexed({ Grid: VISIBLE_GRID, A: HIDDEN_GRID, B: VERY_HIDDEN_LEGEND })
      )
    )
    expect(text).toContain('2 hidden sheets skipped')
    expect(text).not.toContain('sheet(s)')
  })

  it('gives the all-hidden document its own copy, not "no products found"', () => {
    const text = coverageText(
      computeSheetCoverage(indexed({ Grid: HIDDEN_GRID, Legend: VERY_HIDDEN_LEGEND }))
    )
    expect(text).toBe('Nothing to extract — all 2 sheet(s) in this document are hidden.')
  })

  it('is empty when there is nothing to report', () => {
    expect(coverageText(null)).toBe('')
  })
})

/**
 * Condition labels shown on the rule rows.
 *
 * Unlabelled keys fall through to an underscore-replaced key, which reads
 * visibly worse beside its labelled siblings — so the label map is the thing
 * that silently rots when a condition field is added on the backend.
 */
describe('formatConditions', () => {
  it('labels the NCB percentage band', () => {
    expect(formatConditions({ ncb_start_range: 25, ncb_end_range: 100 }))
      .toBe('NCB from: 25 · NCB to: 100')
  })

  it('renders the base twin band including a zero start', () => {
    // 0 is falsy — a truthiness check instead of a null check would drop it.
    expect(formatConditions({ ncb_start_range: 0, ncb_end_range: 24 }))
      .toBe('NCB from: 0 · NCB to: 24')
  })

  it('renders the derived NCB boolean alongside the band', () => {
    expect(formatConditions({ ncb: true, ncb_start_range: 25, ncb_end_range: 100 }))
      .toBe('NCB: yes · NCB from: 25 · NCB to: 100')
  })

  it('labels the keys that previously fell through to the raw key', () => {
    expect(formatConditions({ policies_count_start_range: 30, policies_count_end_range: 100 }))
      .toBe('policies from: 30 · policies to: 100')
    expect(formatConditions({ vehicle_make_exclude: ['TATA'] }))
      .toBe('make excl: TATA')
  })

  it('skips nulls and returns All for an empty or absent condition set', () => {
    expect(formatConditions({ ncb_start_range: null, ncb_end_range: undefined })).toBe('All')
    expect(formatConditions(null)).toBe('All')
    expect(formatConditions({})).toBe('All')
  })

  it('still falls back to the humanised key for an unknown field', () => {
    expect(formatConditions({ some_new_field: 'x' })).toBe('some new field: x')
  })
})

/**
 * Self-declared indicative sheets — a sheet that carries real rates and still
 * is not the payout grid, because its own text says so. See
 * `.claude/rules/cins.md` and `cins/processors/extractors/indicative_markers.py`.
 */
const VISIBLE_HERO_CELLS = { sheet_type: 'indicative_data', visibility: 'visible' }

describe('indicative sheets', () => {
  it('gives indicative_data its own badge, not the unknown fallback', () => {
    const doc = indexed({ 'hero cells': VISIBLE_HERO_CELLS, Mystery: {} })
    const indicativeBadge = getSheetClassBadge('hero cells', doc)
    const unknownBadge = getSheetClassBadge('Mystery', doc)

    expect(indicativeBadge).toContain('amber')
    // The point of the arm: a deliberate classification must not render
    // identically to "we have no idea", which reads as a pipeline failure.
    expect(indicativeBadge).not.toBe(unknownBadge)
  })

  it('does not count an indicative sheet as extracted when a commission sheet exists', () => {
    const coverage = computeSheetCoverage(
      indexed({ Grid: VISIBLE_GRID, 'hero cells': VISIBLE_HERO_CELLS })
    )
    expect(coverage).toMatchObject({ total: 2, extracted: 1, referenceCount: 1 })
  })

  it('does not resurrect an indicative sheet through the no-commission fallback', () => {
    // The case that matters. With no commission sheet the backend falls back to
    // every ROUTABLE sheet — hidden AND indicative removed. Mirroring only the
    // hidden half would make this UI claim the one-pager was extracted, on
    // every document whose only rate-bearing sheet is a one-pager.
    const coverage = computeSheetCoverage(
      indexed({ 'hero cells': VISIBLE_HERO_CELLS, Legend: VISIBLE_LEGEND })
    )
    // Legend is extracted via the fallback; hero cells is not.
    expect(coverage).toMatchObject({ total: 2, extracted: 1, referenceCount: 1 })
  })

  it('surfaces the disclaimer sentence that caused the reclassification', () => {
    const doc = indexed({
      'hero cells': {
        ...VISIBLE_HERO_CELLS,
        override: { excerpt: 'this sheet is just to identify the potential products' },
      },
    })
    expect(getSheetOverrideReason('hero cells', doc)).toContain('potential products')
    expect(getSheetOverrideReason('missing', doc)).toBe('')
  })
})
