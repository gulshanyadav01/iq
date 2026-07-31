<template>
  <div class="p-8">
    <div class="max-w-6xl mx-auto space-y-6">
      <!-- Page Header -->
      <div class="flex items-center justify-between">
        <div>
          <h1 class="text-xl font-semibold tracking-tight text-gray-900 dark:text-white">
            Commission Intake
          </h1>
          <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
            Upload commission documents and track extraction pipeline progress
          </p>
        </div>
        <button
          class="bg-primary-600 hover:bg-primary-700 text-white px-4 py-2 rounded-lg text-sm font-medium transition duration-200"
          @click="isModalOpen = true"
        >
          New Upload
        </button>
      </div>

      <!-- Tab bar -->
      <div class="flex gap-1 border-b border-gray-200 dark:border-gray-800">
        <NuxtLink
          to="/commission-intake"
          class="px-4 py-2 text-xs font-medium border-b-2 transition"
          :class="$route.path === '/commission-intake' ? 'border-primary-500 text-primary-600 dark:text-primary-400' : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'"
        >
          Documents
        </NuxtLink>
        <NuxtLink
          to="/commission-intake/dashboard"
          class="px-4 py-2 text-xs font-medium border-b-2 transition"
          :class="$route.path === '/commission-intake/dashboard' ? 'border-primary-500 text-primary-600 dark:text-primary-400' : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'"
        >
          Dashboard
        </NuxtLink>
      </div>

      <!-- Metric Cards -->
      <div v-if="!loading" class="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <div class="glass rounded-2xl p-5 space-y-1">
          <p class="text-[10px] uppercase tracking-widest text-gray-400 dark:text-gray-500">Total</p>
          <p class="text-2xl font-semibold text-gray-900 dark:text-white">{{ metrics.total }}</p>
          <p class="text-xs text-gray-400">all time</p>
        </div>
        <div class="glass rounded-2xl p-5 space-y-1">
          <p class="text-[10px] uppercase tracking-widest text-green-500">Completed</p>
          <p class="text-2xl font-semibold text-green-600 dark:text-green-400">{{ metrics.completed }}</p>
          <p class="text-xs text-gray-400">fully extracted</p>
        </div>
        <div class="glass rounded-2xl p-5 space-y-1">
          <p class="text-[10px] uppercase tracking-widest text-red-500">Failed</p>
          <p class="text-2xl font-semibold text-red-600 dark:text-red-400">{{ metrics.failed }}</p>
          <p class="text-xs text-gray-400">need attention</p>
        </div>
        <div class="glass rounded-2xl p-5 space-y-1">
          <p class="text-[10px] uppercase tracking-widest text-blue-500">In Progress</p>
          <p class="text-2xl font-semibold text-blue-600 dark:text-blue-400">{{ metrics.inProgress }}</p>
          <p class="text-xs text-gray-400">processing now</p>
        </div>
      </div>
      <div v-else class="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <div v-for="i in 4" :key="i" class="glass rounded-2xl p-5 space-y-2 animate-pulse">
          <div class="h-2 bg-gray-200 dark:bg-gray-700 rounded w-16" />
          <div class="h-7 bg-gray-200 dark:bg-gray-700 rounded w-10" />
          <div class="h-2 bg-gray-200 dark:bg-gray-700 rounded w-24" />
        </div>
      </div>

      <!-- Loading State -->
      <div v-if="loading" class="glass rounded-2xl p-8">
        <div class="flex items-center justify-center gap-2 text-sm text-gray-600 dark:text-gray-400">
          <span class="inline-block animate-spin">⟳</span>
          Loading documents...
        </div>
      </div>

      <!-- Error State -->
      <div v-else-if="error" class="glass rounded-2xl p-8">
        <p class="text-sm text-red-600 dark:text-red-400 text-center">
          Failed to load documents
        </p>
      </div>

      <!-- Empty State -->
      <div v-else-if="documents.length === 0" class="glass rounded-2xl p-12">
        <div class="text-center space-y-3">
          <p class="text-sm text-gray-600 dark:text-gray-400">
            No commission documents uploaded yet.
          </p>
          <button
            class="text-xs font-medium text-primary-600 hover:text-primary-700 dark:text-primary-400 dark:hover:text-primary-300 transition"
            @click="isModalOpen = true"
          >
            Upload your first document →
          </button>
        </div>
      </div>

      <!-- Documents Table -->
      <div v-else class="glass rounded-2xl overflow-hidden">
        <div class="overflow-x-auto">
          <table class="w-full">
            <thead>
              <tr class="border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50">
                <th class="text-left px-4 py-3 text-xs font-semibold uppercase tracking-widest text-gray-600 dark:text-gray-400">
                  Filename
                </th>
                <th class="text-left px-4 py-3 text-xs font-semibold uppercase tracking-widest text-gray-600 dark:text-gray-400">
                  Provider
                </th>
                <th class="text-left px-4 py-3 text-xs font-semibold uppercase tracking-widest text-gray-600 dark:text-gray-400">
                  Type
                </th>
                <th class="text-left px-4 py-3 text-xs font-semibold uppercase tracking-widest text-gray-600 dark:text-gray-400">
                  Period
                </th>
                <th class="text-left px-4 py-3 text-xs font-semibold uppercase tracking-widest text-gray-600 dark:text-gray-400">
                  Status
                </th>
                <th class="text-left px-4 py-3 text-xs font-semibold uppercase tracking-widest text-gray-600 dark:text-gray-400">
                  Submitted
                </th>
                <th class="px-4 py-3 w-10" />
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-200 dark:divide-gray-700">
              <tr
                v-for="doc in documents"
                :key="doc.id"
                class="hover:bg-gray-50/50 dark:hover:bg-gray-800/50 transition cursor-pointer"
                @click="navigateToDetail(doc.id)"
              >
                <td class="px-4 py-3 text-sm text-gray-900 dark:text-gray-100 max-w-xs">
                  <div class="flex items-center gap-1.5 min-w-0">
                    <span class="truncate">{{ doc.original_filename }}</span>
                    <UIcon
                      v-if="doc.is_validated"
                      name="i-lucide-shield-check"
                      class="w-3.5 h-3.5 flex-shrink-0 text-green-500"
                      title="Manually validated"
                    />
                  </div>
                </td>
                <td class="px-4 py-3 text-sm text-gray-600 dark:text-gray-400">
                  {{ doc.provider || '—' }}
                </td>
                <td class="px-4 py-3">
                  <span v-if="doc.source_type" class="px-2 py-0.5 rounded text-xs font-medium bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 uppercase">
                    {{ doc.source_type }}
                  </span>
                  <span v-else class="text-gray-400 text-sm">—</span>
                </td>
                <td class="px-4 py-3 text-sm text-gray-600 dark:text-gray-400 max-w-[8rem] truncate" :title="doc.applicable_period || ''">
                  <template v-if="doc.applicable_period">{{ doc.applicable_period }}</template>
                  <span v-else class="text-gray-400 text-sm">—</span>
                </td>
                <td class="px-4 py-3">
                  <span :class="['inline-flex items-center gap-1.5 px-2 py-1 rounded-full text-xs font-medium', getStatusBadgeClass(doc.status)]">
                    <span v-if="isInFlight(doc.status)" class="w-1.5 h-1.5 rounded-full bg-current animate-pulse" />
                    {{ formatStatus(doc.status) }}
                  </span>
                </td>
                <td class="px-4 py-3 text-sm text-gray-600 dark:text-gray-400">
                  {{ formatDate(doc.created_at) }}
                </td>
                <td class="px-4 py-3 text-right" @click.stop>
                  <div class="flex items-center justify-end gap-1">
                    <button
                      v-if="doc.status === 'failed'"
                      class="p-1.5 rounded hover:bg-red-100 dark:hover:bg-red-900/30 text-red-400 hover:text-red-600 dark:hover:text-red-400 transition disabled:opacity-40 disabled:cursor-not-allowed"
                      title="Retry processing"
                      :disabled="retryingIds.has(doc.id)"
                      @click="retryDocument(doc.id)"
                    >
                      <UIcon
                        name="i-lucide-refresh-cw"
                        class="w-4 h-4"
                        :class="{ 'animate-spin': retryingIds.has(doc.id) }"
                      />
                    </button>
                    <button
                      class="p-1.5 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition"
                      title="View details"
                      @click="navigateToDetail(doc.id)"
                    >
                      <UIcon name="i-lucide-eye" class="w-4 h-4" />
                    </button>
                    <a
                      :href="`${appBaseUrl}/commission-intake/${doc.id}`"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="p-1.5 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition"
                      title="Open in new tab"
                    >
                      <UIcon name="i-lucide-external-link" class="w-4 h-4" />
                    </a>
                    <button
                      class="p-1.5 rounded hover:bg-red-100 dark:hover:bg-red-900/30 text-gray-400 hover:text-red-600 dark:hover:text-red-400 transition disabled:opacity-40 disabled:cursor-not-allowed"
                      title="Delete document"
                      :disabled="deletingIds.has(doc.id)"
                      @click="confirmDelete(doc)"
                    >
                      <UIcon name="i-lucide-trash-2" class="w-4 h-4" />
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Delete Confirm Modal -->
    <UModal v-model:open="isDeleteModalOpen" title="Delete Document">
      <template #body>
        <p class="text-sm text-gray-600 dark:text-gray-400">
          Are you sure you want to delete <span class="font-medium text-gray-900 dark:text-white">{{ documentToDelete?.original_filename }}</span>? This action cannot be undone.
        </p>
      </template>
      <template #footer>
        <div class="flex justify-end gap-3">
          <button
            class="px-4 py-2 rounded-lg text-sm font-medium bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 transition duration-200"
            @click="isDeleteModalOpen = false"
          >
            Cancel
          </button>
          <button
            class="px-4 py-2 rounded-lg text-sm font-medium bg-red-600 hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed text-white transition duration-200"
            :disabled="documentToDelete ? deletingIds.has(documentToDelete.id) : false"
            @click="deleteDocument"
          >
            <span v-if="!documentToDelete || !deletingIds.has(documentToDelete.id)">Delete</span>
            <span v-else class="flex items-center gap-2">
              <span class="inline-block animate-spin">⟳</span>
              Deleting...
            </span>
          </button>
        </div>
      </template>
    </UModal>

    <!-- New Upload Modal -->
    <UModal v-model:open="isModalOpen" title="New Commission Upload" :dismissible="!uploading">
      <template #body>
        <form class="space-y-4" @submit.prevent="handleUpload">
          <!-- File Input -->
          <div>
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
              Commission Document <span class="text-gray-400 font-normal">.eml, .pdf, .xlsx</span>
            </label>
            <input
              type="file"
              accept=".eml,.pdf,.xlsx,.xls"
              class="block w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent dark:bg-gray-700 dark:text-white disabled:bg-gray-100 dark:disabled:bg-gray-800 disabled:cursor-not-allowed"
              :disabled="uploading"
              @change="handleFileSelect"
            >
            <p v-if="selectedFile" class="mt-2 text-xs text-gray-600 dark:text-gray-400">
              Selected: {{ selectedFile.name }} ({{ formatFileSize(selectedFile.size) }})
            </p>
          </div>

          <!-- LLM Provider -->
          <div>
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
              LLM Provider
            </label>
            <USelect
              v-model="form.llm_provider"
              :items="[{ label: 'Gemini', value: 'gemini' }, { label: 'OpenAI', value: 'openai' }]"
              value-key="value"
              :disabled="uploading"
              class="w-full"
            />
          </div>

          <!-- Model (optional) -->
          <div>
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
              Model <span class="text-gray-400 font-normal">(optional)</span>
            </label>
            <input
              v-model="form.llm_model"
              type="text"
              placeholder="Leave blank for default"
              class="block w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 dark:bg-gray-700 dark:text-white disabled:opacity-50 placeholder-gray-400"
              :disabled="uploading"
            >
          </div>

          <!-- Excel Mode -->
          <div>
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
              Excel Processing Mode
            </label>
            <USelect
              v-model="form.excel_mode"
              :items="[{ label: 'Sheet by Sheet', value: 'sheet_by_sheet' }, { label: 'Combined', value: 'combined' }]"
              value-key="value"
              :disabled="uploading"
              class="w-full"
            />
          </div>

          <!-- Processing Mode -->
          <div>
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
              Processing Mode
            </label>
            <div class="flex rounded-lg border border-gray-300 dark:border-gray-600 overflow-hidden">
              <button
                v-for="opt in processingModeOptions"
                :key="opt.value"
                type="button"
                :disabled="uploading || opt.disabled"
                :class="[
                  'flex-1 px-3 py-2 text-xs font-medium transition',
                  form.processing_mode === opt.value
                    ? 'bg-primary-600 text-white'
                    : 'bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-600',
                  opt.disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer',
                ]"
                @click="!opt.disabled && (form.processing_mode = opt.value)"
              >
                {{ opt.label }}
              </button>
            </div>
            <p class="mt-1 text-xs text-gray-400">
              {{ processingModeOptions.find(o => o.value === form.processing_mode)?.hint }}
            </p>
          </div>

          <!-- Error Message -->
          <div v-if="uploadError" class="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-400 px-4 py-3 rounded-lg text-sm">
            {{ uploadError }}
          </div>
        </form>
      </template>

      <template #footer>
        <div class="flex justify-end gap-3">
          <button
            class="px-4 py-2 rounded-lg text-sm font-medium bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 transition duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
            :disabled="uploading"
            @click="closeModal"
          >
            Cancel
          </button>
          <button
            class="px-4 py-2 rounded-lg text-sm font-medium bg-primary-600 hover:bg-primary-700 disabled:bg-gray-400 disabled:cursor-not-allowed text-white transition duration-200"
            :disabled="!selectedFile || uploading"
            @click="handleUpload"
          >
            <span v-if="!uploading">Upload</span>
            <span v-else class="flex items-center gap-2">
              <span class="inline-block animate-spin">⟳</span>
              Uploading...
            </span>
          </button>
        </div>
      </template>
    </UModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'

interface CommissionDocument {
  id: string
  status: 'initiated' | 'indexing' | 'indexed' | 'extracting' | 'completed' | 'failed'
  processing_mode: 'full' | 'index_only' | 'extract_only'
  index_progress: { step: string; detail: string } | null
  original_filename: string
  source_type: string
  provider: string
  llm_provider: string
  llm_model: string
  excel_mode: string
  applicable_period: string | null
  is_validated: boolean
  error: string | null
  created_at: string
  updated_at: string
}

definePageMeta({
  layout: 'default',
  middleware: 'auth'
})

const router = useRouter()
const userStore = useUserStore()
const { getBaseUrl } = useBaseUrl()
const appBaseUrl = useRuntimeConfig().public.appBaseUrl as string

const documents = ref<CommissionDocument[]>([])
const loading = ref(false)
const error = ref(false)

const metrics = computed(() => ({
  total: documents.value.length,
  completed: documents.value.filter(d => d.status === 'completed').length,
  failed: documents.value.filter(d => d.status === 'failed').length,
  inProgress: documents.value.filter(d => isInFlight(d.status)).length,
}))

const isModalOpen = ref(false)
const selectedFile = ref<File | null>(null)
const uploading = ref(false)
const uploadError = ref('')

const form = ref({
  llm_provider: 'gemini',
  llm_model: '',
  excel_mode: 'sheet_by_sheet',
  processing_mode: 'full',
})

const processingModeOptions = [
  { value: 'full', label: 'Index + Extract', disabled: false, hint: 'Full pipeline: index, extract, and validate.' },
  { value: 'index_only', label: 'Index Only', disabled: false, hint: 'Document will be indexed but not extracted. You can trigger extraction later.' },
  { value: 'extract_only', label: 'Extract Only', disabled: true, hint: '' },
]

const retryingIds = ref<Set<string>>(new Set())
const deletingIds = ref<Set<string>>(new Set())
const isDeleteModalOpen = ref(false)
const documentToDelete = ref<CommissionDocument | null>(null)

let pollTimer: ReturnType<typeof setInterval> | null = null

async function getAuthHeaders() {
  const token = await userStore.getToken()
  return { Authorization: `Bearer ${token}` }
}

async function loadDocuments() {
  loading.value = true
  error.value = false
  try {
    const headers = await getAuthHeaders()
    documents.value = await $fetch<CommissionDocument[]>(
      `${getBaseUrl()}/api/commission-intake/documents/`,
      { headers }
    )
  } catch (err) {
    console.error('Failed to load documents:', err)
    error.value = true
  } finally {
    loading.value = false
  }
}

function handleFileSelect(e: Event) {
  const input = e.target as HTMLInputElement
  selectedFile.value = input.files?.[0] || null
  uploadError.value = ''
}

function closeModal() {
  isModalOpen.value = false
  selectedFile.value = null
  uploadError.value = ''
  form.value = { llm_provider: 'gemini', llm_model: '', excel_mode: 'sheet_by_sheet', processing_mode: 'full' }
}

async function handleUpload() {
  if (!selectedFile.value) return
  uploadError.value = ''
  uploading.value = true
  try {
    const formData = new FormData()
    formData.append('file', selectedFile.value)
    formData.append('llm_provider', form.value.llm_provider)
    formData.append('excel_mode', form.value.excel_mode)
    formData.append('processing_mode', form.value.processing_mode)
    if (form.value.llm_model) formData.append('llm_model', form.value.llm_model)

    const headers = await getAuthHeaders()
    const newDoc = await $fetch<CommissionDocument>(
      `${getBaseUrl()}/api/commission-intake/documents/`,
      { method: 'POST', headers, body: formData }
    )
    documents.value.unshift(newDoc)
    closeModal()
    router.push(`/commission-intake/${newDoc.id}`)
  } catch (err) {
    uploadError.value = err instanceof Error ? err.message : 'Upload failed'
  } finally {
    uploading.value = false
  }
}

async function retryDocument(id: string) {
  retryingIds.value = new Set([...retryingIds.value, id])
  try {
    const headers = await getAuthHeaders()
    const updated = await $fetch<CommissionDocument>(
      `${getBaseUrl()}/api/commission-intake/documents/${id}/retry/`,
      { method: 'POST', headers }
    )
    const idx = documents.value.findIndex(d => d.id === id)
    if (idx !== -1) documents.value[idx] = updated
  } catch (err) {
    console.error('Retry failed:', err)
  } finally {
    retryingIds.value = new Set([...retryingIds.value].filter(x => x !== id))
  }
}

function confirmDelete(doc: CommissionDocument) {
  documentToDelete.value = doc
  isDeleteModalOpen.value = true
}

async function deleteDocument() {
  if (!documentToDelete.value) return
  const id = documentToDelete.value.id
  deletingIds.value = new Set([...deletingIds.value, id])
  try {
    const headers = await getAuthHeaders()
    await $fetch(
      `${getBaseUrl()}/api/commission-intake/documents/${id}/`,
      { method: 'DELETE', headers }
    )
    documents.value = documents.value.filter(d => d.id !== id)
    isDeleteModalOpen.value = false
    documentToDelete.value = null
  } catch (err) {
    console.error('Delete failed:', err)
  } finally {
    deletingIds.value = new Set([...deletingIds.value].filter(x => x !== id))
  }
}

function navigateToDetail(id: string) {
  router.push(`/commission-intake/${id}`)
}

function isInFlight(status: string): boolean {
  return status === 'indexing' || status === 'extracting' || status === 'initiated'
}

function getStatusBadgeClass(status: string): string {
  switch (status) {
    case 'initiated':
      return 'bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-400'
    case 'indexing':
      return 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400'
    case 'indexed':
      return 'bg-teal-100 dark:bg-teal-900/30 text-teal-700 dark:text-teal-400'
    case 'extracting':
      return 'bg-indigo-100 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-400'
    case 'completed':
      return 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'
    case 'failed':
      return 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400'
    default:
      return 'bg-gray-100 dark:bg-gray-900/30 text-gray-700 dark:text-gray-400'
  }
}

function formatStatus(status: string): string {
  return status.charAt(0).toUpperCase() + status.slice(1)
}

function formatDate(dateString: string): string {
  const date = new Date(dateString)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)
  if (diffMins < 1) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays < 7) return `${diffDays}d ago`
  return date.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function formatFileSize(bytes: number): string {
  const units = ['B', 'KB', 'MB', 'GB']
  let size = bytes
  let unitIndex = 0
  while (size >= 1024 && unitIndex < units.length - 1) { size /= 1024; unitIndex++ }
  return `${size.toFixed(1)} ${units[unitIndex]}`
}

async function pollInFlight() {
  const inFlight = documents.value.filter(d => isInFlight(d.status))
  if (inFlight.length === 0) return
  try {
    const headers = await getAuthHeaders()
    await Promise.all(inFlight.map(async (doc) => {
      const fresh = await $fetch<CommissionDocument>(
        `${getBaseUrl()}/api/commission-intake/documents/${doc.id}/`,
        { headers }
      )
      const idx = documents.value.findIndex(d => d.id === doc.id)
      if (idx !== -1) documents.value[idx] = fresh
    }))
  } catch (err) {
    console.error('Polling error:', err)
  }
}

onMounted(() => {
  loadDocuments()
  pollTimer = setInterval(pollInFlight, 4000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>
