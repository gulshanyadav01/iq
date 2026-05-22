import { useBaseUrl } from '~/composables/useBaseUrl'
import { useUserStore } from '~/stores/user'
import type { Pipeline, PipelineRun } from '~/types/pipelines'

// $fetch — usable from any context (lifecycle, async, callbacks).
// Avoid useFetch here: HttpClient.get() wraps useFetch, which is only valid
// during component setup. Calling it from onMounted/async paths silently fails
// and breaks the page.

const API = '/api/pipelines'

async function authHeaders(): Promise<Record<string, string>> {
  const userStore = useUserStore()
  const token = await userStore.getToken()
  const h: Record<string, string> = {}
  if (token) h.Authorization = `Bearer ${token}`
  return h
}

export async function getAuthToken(): Promise<string | null> {
  const userStore = useUserStore()
  return (await userStore.getToken()) ?? null
}

function url(path: string): string {
  const { getBaseUrl } = useBaseUrl()
  return `${getBaseUrl()}${API}${path}`
}

export interface ApiResult<T> {
  data: T | null
  error: Error | null
}

async function safe<T>(fn: () => Promise<T>): Promise<ApiResult<T>> {
  try {
    return { data: await fn(), error: null }
  } catch (e) {
    return { data: null, error: e as Error }
  }
}

export const pipelinesApi = {
  list: () =>
    safe(async () =>
      $fetch<Pipeline[] | { results: Pipeline[] }>(url('/pipelines/'), {
        headers: await authHeaders(),
      }),
    ),

  get: (slug: string) =>
    safe(async () =>
      $fetch<Pipeline>(url(`/pipelines/${slug}/`), {
        headers: await authHeaders(),
      }),
    ),

  listRuns: () =>
    safe(async () =>
      $fetch<PipelineRun[] | { results: PipelineRun[] }>(url('/runs/'), {
        headers: await authHeaders(),
      }),
    ),

  getRun: (runId: string) =>
    safe(async () =>
      $fetch<PipelineRun>(url(`/runs/${runId}/`), {
        headers: await authHeaders(),
      }),
    ),

  createRun: (body: FormData) =>
    safe(async () =>
      $fetch<PipelineRun>(url('/runs/'), {
        method: 'POST',
        headers: await authHeaders(),
        body,
      }),
    ),
}

/**
 * Build the artifact URL the browser can GET directly (PDF.js + raw MD).
 * Streams from MinIO via the Django proxy, scoped to the user.
 * Note: caller must attach `Authorization: Bearer …` for non-cookie auth.
 * For PDF.js (which uses XHR internally), we set withCredentials and rely
 * on session cookie OR token in URL — current setup uses a session cookie
 * via Better Auth, which travels automatically.
 */
export function artifactUrl(
  runId: string,
  kind: 'pdf' | 'md' | 'blocks' | 'llm_raw' | 'llm_extracted',
): string {
  const { getBaseUrl } = useBaseUrl()
  return `${getBaseUrl()}/api/pipelines/runs/${runId}/artifacts/${kind}/`
}
