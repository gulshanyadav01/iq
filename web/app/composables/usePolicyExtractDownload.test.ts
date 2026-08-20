import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { usePolicyExtractDownload } from './usePolicyExtractDownload'

/**
 * Per-row document download for the Policy Extract list.
 *
 * The document endpoint is Bearer-authed and serves preview-safe types inline,
 * so the composable must (a) fetch the bytes as a blob with the auth header and
 * (b) force a save-to-disk with `a.download`. The row's busy state must flip on
 * for exactly the id being fetched and always clear afterwards — success OR
 * failure — or a failed download would strand the button disabled forever.
 *
 * The composable calls Nuxt auto-imports (`useUserStore`, `useBaseUrl`,
 * `$fetch`) and browser globals (`document`, `URL`) that don't exist in the
 * node test env, so we stub each one and assert against the real code path.
 */

interface AnchorStub {
  href: string
  download: string
  click: ReturnType<typeof vi.fn>
}

let anchor: AnchorStub
let getToken: ReturnType<typeof vi.fn>
let fetchMock: ReturnType<typeof vi.fn>
let createObjectURL: ReturnType<typeof vi.fn>
let revokeObjectURL: ReturnType<typeof vi.fn>
let createElement: ReturnType<typeof vi.fn>

const BLOB = new Blob(['%PDF-1.7'], { type: 'application/pdf' })

beforeEach(() => {
  anchor = { href: '', download: '', click: vi.fn() }
  getToken = vi.fn().mockResolvedValue('tok-123')
  fetchMock = vi.fn().mockResolvedValue(BLOB)
  createObjectURL = vi.fn().mockReturnValue('blob:obj-url')
  revokeObjectURL = vi.fn()
  createElement = vi.fn().mockReturnValue(anchor)

  vi.stubGlobal('useUserStore', () => ({ getToken }))
  vi.stubGlobal('useBaseUrl', () => ({ getBaseUrl: () => 'http://api.test' }))
  vi.stubGlobal('$fetch', fetchMock)
  vi.stubGlobal('document', { createElement })
  vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const req = { id: 'pext-1', original_filename: 'policy.pdf' }

describe('usePolicyExtractDownload — success path', () => {
  it('fetches the document as a blob with the Bearer header and the id-scoped URL', async () => {
    const { downloadDocument } = usePolicyExtractDownload()
    await downloadDocument(req)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, opts] = fetchMock.mock.calls[0]
    expect(url).toBe('http://api.test/api/policy-extract/requests/pext-1/document/')
    expect(opts).toMatchObject({
      method: 'GET',
      responseType: 'blob',
      headers: { Authorization: 'Bearer tok-123' },
    })
  })

  it('forces a save-to-disk under the original filename and revokes the object URL', async () => {
    const { downloadDocument } = usePolicyExtractDownload()
    await downloadDocument(req)

    expect(createObjectURL).toHaveBeenCalledWith(BLOB)
    expect(anchor.href).toBe('blob:obj-url')
    // a.download overrides the server's inline Content-Disposition for PDFs.
    expect(anchor.download).toBe('policy.pdf')
    expect(anchor.click).toHaveBeenCalledTimes(1)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:obj-url')
  })

  it('leaves no row marked as downloading once finished', async () => {
    const { downloadingIds, downloadDocument } = usePolicyExtractDownload()
    await downloadDocument(req)
    expect(downloadingIds.value.has('pext-1')).toBe(false)
    expect(downloadingIds.value.size).toBe(0)
  })
})

describe('usePolicyExtractDownload — busy state', () => {
  it('marks the row as downloading while the fetch is in flight, then clears it', async () => {
    let release!: (blob: Blob) => void
    fetchMock.mockReturnValue(new Promise<Blob>((resolve) => { release = resolve }))

    const { downloadingIds, downloadDocument } = usePolicyExtractDownload()
    const pending = downloadDocument(req)

    // Mid-flight: the button for this row is disabled and its icon pulses.
    expect(downloadingIds.value.has('pext-1')).toBe(true)

    release(BLOB)
    await pending
    expect(downloadingIds.value.has('pext-1')).toBe(false)
  })

  it('clears only the finished row, leaving a concurrent download marked', async () => {
    let release!: (blob: Blob) => void
    // First call hangs, second resolves immediately.
    fetchMock
      .mockReturnValueOnce(new Promise<Blob>((resolve) => { release = resolve }))
      .mockResolvedValueOnce(BLOB)

    const { downloadingIds, downloadDocument } = usePolicyExtractDownload()
    const slow = downloadDocument({ id: 'slow', original_filename: 'a.pdf' })
    await downloadDocument({ id: 'fast', original_filename: 'b.pdf' })

    // The fast row cleared; the slow row is still in flight.
    expect(downloadingIds.value.has('fast')).toBe(false)
    expect(downloadingIds.value.has('slow')).toBe(true)

    release(BLOB)
    await slow
    expect(downloadingIds.value.has('slow')).toBe(false)
  })
})

describe('usePolicyExtractDownload — failure path', () => {
  it('swallows a 404/fetch error, logs it, and never triggers a download', async () => {
    fetchMock.mockRejectedValue(new Error('404'))

    const { downloadDocument } = usePolicyExtractDownload()
    // The button handler must not reject — an unhandled rejection would surface
    // to the user as a console/overlay error for a normal "not stored yet" case.
    await expect(downloadDocument(req)).resolves.toBeUndefined()

    expect(console.error).toHaveBeenCalledWith('Download failed:', expect.any(Error))
    expect(anchor.click).not.toHaveBeenCalled()
    expect(createObjectURL).not.toHaveBeenCalled()
  })

  it('re-enables the row after a failure so the user can retry', async () => {
    fetchMock.mockRejectedValue(new Error('boom'))

    const { downloadingIds, downloadDocument } = usePolicyExtractDownload()
    await downloadDocument(req)
    expect(downloadingIds.value.has('pext-1')).toBe(false)
  })

  it('treats a 0-byte body as a failure instead of saving an empty file', async () => {
    // A 200 with an empty stream would otherwise be written to disk as a valid
    // "policy.pdf". Guard it before an object URL or anchor is ever created.
    fetchMock.mockResolvedValue(new Blob([]))

    const { downloadingIds, downloadDocument } = usePolicyExtractDownload()
    await downloadDocument(req)

    expect(createObjectURL).not.toHaveBeenCalled()
    expect(anchor.click).not.toHaveBeenCalled()
    expect(console.error).toHaveBeenCalledWith('Download failed:', expect.any(Error))
    expect(downloadingIds.value.has('pext-1')).toBe(false)
  })
})
