<template>
  <div class="p-8">
    <div class="max-w-6xl mx-auto space-y-6">
      <!-- Page Header -->
      <div class="flex items-center justify-between">
        <div>
          <h1 class="text-xl font-semibold tracking-tight text-gray-900 dark:text-white">
            Policy Extract
          </h1>
          <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
            Upload policy documents and monitor extraction status
          </p>
        </div>
        <button
          class="bg-primary-600 hover:bg-primary-700 text-white px-4 py-2 rounded-lg text-sm font-medium transition duration-200"
          @click="isModalOpen = true"
        >
          New Request
        </button>
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
          <p class="text-xs text-gray-400">successfully extracted</p>
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
          Loading requests...
        </div>
      </div>

      <!-- Error State -->
      <div v-else-if="error" class="glass rounded-2xl p-8">
        <p class="text-sm text-red-600 dark:text-red-400 text-center">
          Failed to load requests
        </p>
      </div>

      <!-- Empty State -->
      <div v-else-if="requests.length === 0" class="glass rounded-2xl p-12">
        <div class="text-center space-y-3">
          <p class="text-sm text-gray-600 dark:text-gray-400">
            No extraction requests yet.
          </p>
          <button
            class="text-xs font-medium text-primary-600 hover:text-primary-700 dark:text-primary-400 dark:hover:text-primary-300 transition"
            @click="isModalOpen = true"
          >
            Create your first request →
          </button>
        </div>
      </div>

      <!-- Requests Table -->
      <div v-else class="glass rounded-2xl overflow-hidden">
        <div class="overflow-x-auto">
          <table class="w-full">
            <thead>
              <tr class="border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50">
                <th class="text-left px-4 py-3 text-xs font-semibold uppercase tracking-widest text-gray-600 dark:text-gray-400">
                  Request ID
                </th>
                <th class="text-left px-4 py-3 text-xs font-semibold uppercase tracking-widest text-gray-600 dark:text-gray-400">
                  Filename
                </th>
                <th class="text-left px-4 py-3 text-xs font-semibold uppercase tracking-widest text-gray-600 dark:text-gray-400">
                  Status
                </th>
                <th class="text-left px-4 py-3 text-xs font-semibold uppercase tracking-widest text-gray-600 dark:text-gray-400">
                  Submitted
                </th>
                <th class="text-left px-4 py-3 text-xs font-semibold uppercase tracking-widest text-gray-600 dark:text-gray-400">
                  Updated
                </th>
                <th class="px-4 py-3 w-10" />
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-200 dark:divide-gray-700">
              <tr
                v-for="req in requests"
                :key="req.id"
                class="hover:bg-gray-50/50 dark:hover:bg-gray-800/50 transition cursor-pointer"
                @click="openDetail(req)"
              >
                <td class="px-4 py-3 text-xs text-gray-600 dark:text-gray-400 font-mono" :title="req.id">
                  {{ req.id }}
                </td>
                <td class="px-4 py-3 text-sm text-gray-900 dark:text-gray-100 max-w-xs truncate">
                  {{ req.original_filename }}
                </td>
                <td class="px-4 py-3">
                  <span :class="['px-2 py-1 rounded-full text-xs font-medium', getStatusBadgeClass(req.status)]">
                    {{ formatStatus(req.status) }}
                  </span>
                </td>
                <td class="px-4 py-3 text-sm text-gray-600 dark:text-gray-400">
                  {{ formatDate(req.created_at) }}
                </td>
                <td class="px-4 py-3 text-sm text-gray-600 dark:text-gray-400">
                  {{ formatDate(req.updated_at) }}
                </td>
                <td class="px-4 py-3 text-right" @click.stop>
                  <button
                    class="p-1.5 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition"
                    title="View result"
                    @click="openDetail(req)"
                  >
                    <UIcon name="i-lucide-eye" class="w-4 h-4" />
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Detail Slide-over -->
    <USlideover v-model:open="detailOpen" :title="selectedRequest?.original_filename ?? 'Details'" side="right" :ui="{ content: 'w-full max-w-3xl' }">
      <template #body>
        <!-- In-flight -->
        <div v-if="selectedRequest?.status === 'initiated' || selectedRequest?.status === 'processing'" class="p-6 flex items-center gap-2 text-gray-500">
          <UIcon name="i-lucide-loader-circle" class="animate-spin" />
          Processing…
        </div>

        <!-- Failed -->
        <div v-else-if="selectedRequest?.status === 'failed'" class="p-6">
          <div class="text-red-600 dark:text-red-400 p-4 bg-red-50 dark:bg-red-900/20 rounded text-sm">
            {{ selectedRequest.error || 'Unknown error' }}
          </div>
        </div>

        <!-- Completed -->
        <div v-else-if="selectedRequest?.status === 'completed'" class="flex flex-col h-full">
          <!-- View toggle -->
          <div class="px-6 pt-4 pb-0 border-b border-gray-200 dark:border-gray-700 flex items-end justify-between">
            <div class="flex gap-4 text-xs font-medium">
              <button
                v-for="v in views"
                :key="v.id"
                :class="['pb-2 border-b-2 transition', activeView === v.id ? 'border-primary-500 text-primary-600 dark:text-primary-400' : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300']"
                @click="activeView = v.id"
              >
                {{ v.label }}
              </button>
            </div>
            <button
              class="mb-2 flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition"
              @click="copyContent"
            >
              <UIcon :name="copied ? 'i-lucide-check' : 'i-lucide-copy'" class="w-3.5 h-3.5" />
              {{ copied ? 'Copied!' : 'Copy' }}
            </button>
          </div>

          <!-- Extracted Fields -->
          <div v-if="activeView === 'fields'" class="flex flex-col flex-1 overflow-hidden">
            <!-- Filter -->
            <div class="px-6 py-3 flex gap-4 text-xs text-gray-600 dark:text-gray-400 border-b border-gray-100 dark:border-gray-800">
              <span class="font-medium text-gray-700 dark:text-gray-300">Show</span>
              <label v-for="f in fieldFilters" :key="f.id" class="flex items-center gap-1 cursor-pointer">
                <input type="radio" :value="f.id" v-model="activeFilter" class="accent-primary-500" />
                {{ f.label }}
              </label>
            </div>
            <!-- Table -->
            <div class="overflow-auto flex-1">
              <table class="w-full text-sm">
                <thead class="sticky top-0 bg-gray-50 dark:bg-gray-800/80">
                  <tr>
                    <th class="text-left px-6 py-2 text-xs font-semibold text-gray-500 uppercase tracking-wider w-1/3">Field</th>
                    <th class="text-left px-6 py-2 text-xs font-semibold text-gray-500 uppercase tracking-wider">Value</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-gray-100 dark:divide-gray-800">
                  <tr v-for="row in filteredFields" :key="row.field">
                    <td class="px-6 py-2 text-xs text-gray-500 dark:text-gray-400 font-mono">{{ row.field }}</td>
                    <td class="px-6 py-2 text-sm text-gray-900 dark:text-gray-100 truncate max-w-[260px]" :title="String(row.value)">
                      {{ row.value !== '' && row.value !== null && row.value !== undefined ? row.value : '—' }}
                    </td>
                  </tr>
                  <tr v-if="filteredFields.length === 0">
                    <td colspan="2" class="px-6 py-6 text-center text-xs text-gray-400">No fields match this filter</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <!-- JSON view -->
          <div v-else-if="activeView === 'json'" class="flex-1 overflow-auto p-6">
            <pre class="text-xs bg-gray-100 dark:bg-gray-800 p-4 rounded whitespace-pre-wrap leading-relaxed">{{ formattedResult }}</pre>
          </div>
        </div>
      </template>
    </USlideover>

    <!-- New Request Modal -->
    <UModal v-model:open="isModalOpen" title="New Extraction Request" :dismissible="!uploading">
      <template #body>
        <form class="space-y-4" @submit.prevent="handleUpload">
          <!-- File Input -->
          <div>
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
              Policy Document
            </label>
            <input
              type="file"
              class="block w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent dark:bg-gray-700 dark:text-white disabled:bg-gray-100 dark:disabled:bg-gray-800 disabled:cursor-not-allowed"
              :disabled="uploading"
              @change="handleFileSelect"
            >
            <p v-if="selectedFile" class="mt-2 text-xs text-gray-600 dark:text-gray-400">
              Selected: {{ selectedFile.name }} ({{ formatFileSize(selectedFile.size) }})
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
            <span v-if="!uploading">Submit</span>
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

interface ExtractionRequest {
  id: string
  status: 'initiated' | 'processing' | 'completed' | 'failed'
  original_filename: string
  metadata: Record<string, unknown> | null
  result: Record<string, unknown> | null
  error: string | null
  created_at: string
  updated_at: string
}

definePageMeta({
  layout: 'default',
  middleware: 'auth'
})

const userStore = useUserStore()
const { getBaseUrl } = useBaseUrl()

// Table state
const requests = ref<ExtractionRequest[]>([])
const loading = ref(false)
const error = ref(false)

const metrics = computed(() => ({
  total: requests.value.length,
  completed: requests.value.filter(r => r.status === 'completed').length,
  failed: requests.value.filter(r => r.status === 'failed').length,
  inProgress: requests.value.filter(r => r.status === 'initiated' || r.status === 'processing').length,
}))

// Modal state
const isModalOpen = ref(false)
const selectedFile = ref<File | null>(null)
const uploading = ref(false)
const uploadError = ref('')

// Detail slide-over state
const selectedRequest = ref<ExtractionRequest | null>(null)
const detailOpen = ref(false)
let pollTimer: ReturnType<typeof setInterval> | null = null

const views: { id: 'fields' | 'json'; label: string }[] = [
  { id: 'fields', label: 'Extracted Fields' },
  { id: 'json', label: 'JSON' },
]
const activeView = ref<'fields' | 'json'>('fields')

const fieldFilters = [
  { id: 'all', label: 'All Fields' },
  { id: 'extracted', label: 'Extracted Only' },
  { id: 'key', label: 'Key Fields' },
]
const activeFilter = ref<'all' | 'extracted' | 'key'>('all')

const KEY_FIELDS = [
  'policy_number', 'insurer_id', 'plan_type_id', 'policy_term', 'business_type',
  'product_id', 'cust_name', 'mobileNo', 'email', 'cust_address', 'reg_num',
  'premium', 'net_premium', 'sum_insured', 'policy_start_date', 'policy_end_date',
  'VehicleClass', 'fuel_type', 'rto_num', 'rto_city',
]

const extractedFields = computed(() => {
  const fields = selectedRequest.value?.result?.extracted_fields as Record<string, { value: unknown }> | undefined
  if (!fields) return []
  return Object.entries(fields).map(([field, data]) => ({
    field,
    value: typeof data === 'object' && data !== null && 'value' in data ? data.value : data,
  }))
})

const filteredFields = computed(() => {
  const all = extractedFields.value
  if (activeFilter.value === 'extracted') {
    return all.filter(r => r.value !== null && r.value !== undefined && r.value !== '' && r.value !== '0' && r.value !== 'NO' && r.value !== 'FALSE')
  }
  if (activeFilter.value === 'key') {
    return all.filter(r => KEY_FIELDS.includes(r.field))
  }
  return all
})

const formattedResult = computed(() => {
  if (!selectedRequest.value?.result) return ''
  const { intermediate_files, ...displayResult } = selectedRequest.value.result as any
  return JSON.stringify(displayResult, null, 2)
})

async function getAuthHeaders() {
  const token = await userStore.getToken()
  return { Authorization: `Bearer ${token}` }
}

async function loadRequests() {
  loading.value = true
  error.value = false

  try {
    const headers = await getAuthHeaders()
    requests.value = await $fetch<ExtractionRequest[]>(`${getBaseUrl()}/api/policy-extract/requests/`, {
      headers
    })
  } catch (err) {
    console.error('Failed to load requests:', err)
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
}

async function handleUpload() {
  if (!selectedFile.value) return

  uploadError.value = ''
  uploading.value = true

  try {
    const formData = new FormData()
    formData.append('file', selectedFile.value)

    const headers = await getAuthHeaders()
    const newRequest = await $fetch<ExtractionRequest>(`${getBaseUrl()}/api/policy-extract/requests/`, {
      method: 'POST',
      headers,
      body: formData
    })

    requests.value.unshift(newRequest)
    closeModal()
  } catch (err) {
    uploadError.value = err instanceof Error ? err.message : 'Upload failed'
  } finally {
    uploading.value = false
  }
}

function getStatusBadgeClass(status: string): string {
  switch (status) {
    case 'initiated':
      return 'bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-400'
    case 'processing':
      return 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400'
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

  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  })
}

function formatFileSize(bytes: number): string {
  const units = ['B', 'KB', 'MB', 'GB']
  let size = bytes
  let unitIndex = 0
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex++
  }
  return `${size.toFixed(2)} ${units[unitIndex]}`
}

const copied = ref(false)

function openDetail(req: ExtractionRequest) {
  selectedRequest.value = req
  detailOpen.value = true
  activeView.value = 'fields'
  activeFilter.value = 'all'
}

async function copyContent() {
  let text = ''
  if (activeView.value === 'json') {
    text = formattedResult.value
  } else {
    text = filteredFields.value.map(r => `${r.field}\t${r.value ?? ''}`).join('\n')
  }
  await navigator.clipboard.writeText(text)
  copied.value = true
  setTimeout(() => { copied.value = false }, 2000)
}

async function pollInFlight() {
  const inFlight = requests.value.filter(
    r => r.status === 'initiated' || r.status === 'processing'
  )

  if (inFlight.length === 0) return

  try {
    const headers = await getAuthHeaders()
    await Promise.all(inFlight.map(async (req) => {
      const fresh = await $fetch<ExtractionRequest>(
        `${getBaseUrl()}/api/policy-extract/requests/${req.id}/`,
        { headers }
      )
      const idx = requests.value.findIndex(r => r.id === req.id)
      if (idx !== -1) requests.value[idx] = fresh
      if (selectedRequest.value?.id === req.id) selectedRequest.value = fresh
    }))
  } catch (err) {
    console.error('Polling error:', err)
  }
}

onMounted(() => {
  loadRequests()
  pollTimer = setInterval(pollInFlight, 4000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>
