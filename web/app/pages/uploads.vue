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
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-200 dark:divide-gray-700">
              <tr
                v-for="req in requests"
                :key="req.id"
                class="hover:bg-gray-50/50 dark:hover:bg-gray-800/50 transition"
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
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

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

          <!-- Metadata (Optional) -->
          <div>
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
              Metadata <span class="text-xs text-gray-500">(optional JSON)</span>
            </label>
            <textarea
              v-model="metadata"
              placeholder='{"document_type": "policy", "user_ref": "abc123"}'
              :disabled="uploading"
              rows="3"
              class="block w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent dark:bg-gray-700 dark:text-white dark:placeholder-gray-500 disabled:bg-gray-100 dark:disabled:bg-gray-800 disabled:cursor-not-allowed font-mono text-sm"
            />
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
import { ref, onMounted } from 'vue'

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

// Modal state
const isModalOpen = ref(false)
const selectedFile = ref<File | null>(null)
const metadata = ref('')
const uploading = ref(false)
const uploadError = ref('')

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
  metadata.value = ''
  uploadError.value = ''
}

async function handleUpload() {
  if (!selectedFile.value) return

  uploadError.value = ''

  if (metadata.value.trim()) {
    try {
      JSON.parse(metadata.value)
    } catch {
      uploadError.value = 'Invalid JSON in metadata field'
      return
    }
  }

  uploading.value = true

  try {
    const formData = new FormData()
    formData.append('file', selectedFile.value)
    if (metadata.value.trim()) {
      formData.append('metadata', metadata.value)
    }

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

onMounted(() => {
  loadRequests()
})
</script>
