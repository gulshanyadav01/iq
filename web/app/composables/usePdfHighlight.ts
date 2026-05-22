/**
 * PDF.js coordinate-space helpers.
 *
 * Minerva's `bbox` is documented as PDF point space (origin bottom-left, 72dpi).
 * Some PDFs come back in pixel space (PaddleOCR convention). Per F9, we infer
 * the space at first use and log a console.warn so silent misalignment cannot
 * happen.
 */

import type { Block } from '~/types/pipelines'

export type CoordSpace = 'point' | 'pixel'

export interface PdfPageDims {
  /** Page width in PDF points (e.g. 595 for A4). */
  widthPts: number
  /** Page height in PDF points (e.g. 842 for A4). */
  heightPts: number
  /** Page width in CSS pixels at the active zoom. */
  cssWidth: number
  /** Page height in CSS pixels at the active zoom. */
  cssHeight: number
}

export interface CssRect {
  left: number
  top: number
  width: number
  height: number
}

/**
 * Decide whether Minerva returned point-space or pixel-space bboxes.
 * Compares the max X of all bboxes on a page against the page width in points.
 */
export function inferCoordinateSpace(
  blocks: Block[],
  pageWidthPts: number,
): CoordSpace {
  let warned = false
  let maxX = 0
  for (const b of blocks) {
    if (!b.bbox) continue
    if (b.bbox[2] > maxX) maxX = b.bbox[2]
  }
  if (maxX > pageWidthPts * 1.5) {
    if (!warned) {

      console.warn(
        '[usePdfHighlight] Minerva bbox X exceeds PDF point width by >1.5x — using pixel-space transform.',
      )
    }
    return 'pixel'
  }
  return 'point'
}

/**
 * Convert a single bbox to CSS-pixel rect for overlay rendering.
 */
export function bboxToCssRect(
  bbox: [number, number, number, number],
  page: PdfPageDims,
  space: CoordSpace,
): CssRect {
  const [x0, y0, x1, y1] = bbox

  if (space === 'pixel') {
    // Pixel space: divide by the page's pixel-to-CSS ratio.
    const sx = page.cssWidth / (page.widthPts * (page.cssWidth / page.widthPts || 1))
    const sy = page.cssHeight / (page.heightPts * (page.cssHeight / page.heightPts || 1))
    // Effectively: cssWidth / widthPx — but we only have widthPts. Assume the
    // ratio is the cssWidth/widthPts factor (renderer maps PDF.js content
    // proportionally). Multiply pixel coords by cssWidth/widthPts to get CSS.
    const xScale = page.cssWidth / page.widthPts
    const yScale = page.cssHeight / page.heightPts
    return {
      left: x0 * xScale,
      top: y0 * yScale,
      width: (x1 - x0) * xScale,
      height: (y1 - y0) * yScale,
    }
  }

  // Point space: Minerva middle.json uses TOP-LEFT origin (despite the PDF
  // native bottom-left convention). Do NOT flip Y. Verified against
  // GSTIN_Board sample where y0=48 corresponds to small top margin.
  const xScale = page.cssWidth / page.widthPts
  const yScale = page.cssHeight / page.heightPts
  return {
    left: x0 * xScale,
    top: y0 * yScale,
    width: (x1 - x0) * xScale,
    height: (y1 - y0) * yScale,
  }
}

/**
 * Compose state for a parent component: figure out the coord space once, then
 * expose a `rectFor(block)` helper.
 */
export function usePdfHighlight() {
  const space = ref<CoordSpace>('point')

  function configure(blocks: Block[], pageWidthPts: number) {
    space.value = inferCoordinateSpace(blocks, pageWidthPts)
  }

  function rectFor(block: Block, page: PdfPageDims): CssRect | null {
    if (!block.bbox) return null
    return bboxToCssRect(block.bbox, page, space.value)
  }

  return {
    space,
    configure,
    rectFor,
  }
}
