import { describe, expect, it } from 'vitest'

import { classifyDocumentKind } from './useDocumentKind'

/**
 * Document preview renderer selection.
 *
 * The viewer picks pdf.js, an <img>, or the "can't preview" fallback from this
 * classification. Extension is the primary signal, but `file_link` uploads are
 * named after the bare policy id (e.g. `PBP10356218`) with no extension — for
 * those the server-resolved MIME type is the only signal, and getting it wrong
 * means a PDF that silently refuses to preview.
 */
describe('classifyDocumentKind', () => {
  describe('by filename extension (primary signal)', () => {
    it('classifies .pdf as pdf', () => {
      expect(classifyDocumentKind('policy.pdf')).toBe('pdf')
    })

    it('is case-insensitive on the extension', () => {
      expect(classifyDocumentKind('POLICY.PDF')).toBe('pdf')
    })

    it.each(['a.png', 'a.jpg', 'a.jpeg', 'a.webp', 'a.gif', 'a.bmp', 'a.svg'])(
      'classifies image extension %s as image',
      (name) => {
        expect(classifyDocumentKind(name)).toBe('image')
      },
    )

    it('extension wins over a conflicting content type', () => {
      expect(classifyDocumentKind('policy.pdf', 'image/png')).toBe('pdf')
    })
  })

  describe('MIME fallback for extensionless filenames', () => {
    it('uses application/pdf content type when the name has no extension', () => {
      expect(classifyDocumentKind('PBP10356218', 'application/pdf')).toBe('pdf')
    })

    it('is case-insensitive on the content type', () => {
      expect(classifyDocumentKind('PBP10356218', 'APPLICATION/PDF')).toBe('pdf')
    })

    it('uses an image/* content type for extensionless names', () => {
      expect(classifyDocumentKind('PBP10356218', 'image/jpeg')).toBe('image')
    })

    it('falls back to other for an unknown MIME type', () => {
      expect(classifyDocumentKind('PBP10356218', 'application/octet-stream')).toBe('other')
    })

    it('falls back to other when no content type is provided', () => {
      expect(classifyDocumentKind('PBP10356218')).toBe('other')
      expect(classifyDocumentKind('PBP10356218', null)).toBe('other')
    })
  })

  describe('unpreviewable types', () => {
    it('classifies a spreadsheet extension as other', () => {
      expect(classifyDocumentKind('report.xlsx')).toBe('other')
    })

    it('handles an empty filename without throwing', () => {
      expect(classifyDocumentKind('')).toBe('other')
    })
  })
})
