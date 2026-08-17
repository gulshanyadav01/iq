export type DocumentKind = 'pdf' | 'image' | 'other'

const IMAGE_EXT = /\.(png|jpe?g|webp|gif|bmp|svg)$/

/**
 * Decide how a document should be previewed.
 *
 * The filename extension is the primary signal. When it's missing or unknown —
 * e.g. `file_link` uploads named after the bare policy id like `PBP10356218` —
 * fall back to the server-resolved MIME type so extensionless PDFs/images still
 * render inline instead of hitting the "can't preview" fallback.
 */
export function classifyDocumentKind(
  filename: string,
  contentType?: string | null,
): DocumentKind {
  const name = (filename ?? '').toLowerCase()
  if (name.endsWith('.pdf')) return 'pdf'
  if (IMAGE_EXT.test(name)) return 'image'

  const mime = (contentType ?? '').toLowerCase()
  if (mime === 'application/pdf') return 'pdf'
  if (mime.startsWith('image/')) return 'image'
  return 'other'
}
