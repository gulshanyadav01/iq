import { ref } from 'vue'

/** The subset of an extraction request needed to fetch and name its document. */
export interface DownloadableRequest {
  id: string
  original_filename: string
}

/**
 * Per-row download of the original uploaded policy document.
 *
 * The document endpoint requires the Bearer header (so a plain `<a href>` can't
 * authenticate) and serves preview-safe types (PDF/images) inline, so we fetch
 * the bytes as a blob and force a save-to-disk via `a.download` rather than
 * relying on the server's Content-Disposition. `downloadingIds` drives the
 * per-row busy state (disabled button + pulsing icon) and is always cleared in
 * `finally`, so a failed download re-enables the row.
 */
export function usePolicyExtractDownload() {
  const userStore = useUserStore()
  const { getBaseUrl } = useBaseUrl()
  const downloadingIds = ref<Set<string>>(new Set())

  async function downloadDocument(req: DownloadableRequest) {
    downloadingIds.value = new Set([...downloadingIds.value, req.id])
    // Hoisted so it is revoked in `finally` even if createElement/click throws
    // after the URL is created — otherwise the blob leaks for the tab's lifetime.
    let objUrl: string | undefined
    try {
      const token = await userStore.getToken()
      const blob = await $fetch<Blob>(
        `${getBaseUrl()}/api/policy-extract/requests/${req.id}/document/`,
        { method: 'GET', headers: { Authorization: `Bearer ${token}` }, responseType: 'blob' },
      )
      // $fetch throws on non-2xx, so this only guards a genuinely empty stored
      // document — saving a 0-byte file would look like a successful download.
      if (blob.size === 0) throw new Error('Document is empty')
      objUrl = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = objUrl
      a.download = req.original_filename
      a.click()
    } catch (err) {
      // 404 = no stored document yet (e.g. a file_link request still being parsed,
      // where source_url is null until the Celery task saves the file). The app has
      // no toast surface, so — like the page's other row actions (retry) — failures
      // are logged only; the button simply re-enables so the user can try again.
      console.error('Download failed:', err)
    } finally {
      if (objUrl) URL.revokeObjectURL(objUrl)
      downloadingIds.value = new Set([...downloadingIds.value].filter(x => x !== req.id))
    }
  }

  return { downloadingIds, downloadDocument }
}
