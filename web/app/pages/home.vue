<template>
  <div class="p-8">
    <div class="max-w-6xl mx-auto space-y-8">
      <!-- User Greeting -->
      <div v-if="userStore.profile" class="text-center mb-4">
        <p class="text-sm font-semibold text-gray-900 dark:text-white">
          Welcome back, {{ userStore.fullName || userStore.profile.email }}
        </p>
      </div>

      <!-- Metrics Dashboard -->
      <div v-if="!loading && !error" class="space-y-4">
        <h2 class="text-sm font-semibold tracking-tight text-gray-900 dark:text-white">
          Policy Extract Statistics
        </h2>

        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          <!-- Total Uploads -->
          <div class="glass rounded-2xl p-6 space-y-2">
            <div class="flex items-center justify-between">
              <p class="text-xs font-semibold uppercase tracking-widest text-gray-600 dark:text-gray-400">
                Total Uploads
              </p>
              <FileText class="h-4 w-4 text-primary-600 dark:text-primary-400" />
            </div>
            <p class="text-2xl font-semibold text-gray-900 dark:text-white">
              {{ uploads.length }}
            </p>
          </div>

          <!-- Completed -->
          <div class="glass rounded-2xl p-6 space-y-2">
            <div class="flex items-center justify-between">
              <p class="text-xs font-semibold uppercase tracking-widest text-gray-600 dark:text-gray-400">
                Completed
              </p>
              <CheckCircle2 class="h-4 w-4 text-green-600 dark:text-green-400" />
            </div>
            <p class="text-2xl font-semibold text-gray-900 dark:text-white">
              {{ completedCount }}
            </p>
            <p class="text-xs text-gray-500 dark:text-gray-500">
              {{ successRate }}% success rate
            </p>
          </div>

          <!-- Processing -->
          <div class="glass rounded-2xl p-6 space-y-2">
            <div class="flex items-center justify-between">
              <p class="text-xs font-semibold uppercase tracking-widest text-gray-600 dark:text-gray-400">
                Processing
              </p>
              <Clock class="h-4 w-4 text-blue-600 dark:text-blue-400" />
            </div>
            <p class="text-2xl font-semibold text-gray-900 dark:text-white">
              {{ processingCount }}
            </p>
          </div>

          <!-- Failed -->
          <div class="glass rounded-2xl p-6 space-y-2">
            <div class="flex items-center justify-between">
              <p class="text-xs font-semibold uppercase tracking-widest text-gray-600 dark:text-gray-400">
                Failed
              </p>
              <AlertCircle class="h-4 w-4 text-red-600 dark:text-red-400" />
            </div>
            <p class="text-2xl font-semibold text-gray-900 dark:text-white">
              {{ failedCount }}
            </p>
          </div>
        </div>

        <!-- Recent Extractions Table -->
        <div v-if="uploads.length > 0" class="mt-8 space-y-4">
          <div class="flex items-center justify-between">
            <h2 class="text-sm font-semibold tracking-tight text-gray-900 dark:text-white">
              Recent Extractions
            </h2>
            <NuxtLink
              to="/uploads"
              class="text-xs font-medium text-primary-600 hover:text-primary-700 dark:text-primary-400 dark:hover:text-primary-300 transition"
            >
              View all →
            </NuxtLink>
          </div>

          <div class="glass rounded-2xl overflow-hidden">
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
                      Uploaded
                    </th>
                    <th class="text-right px-4 py-3 text-xs font-semibold uppercase tracking-widest text-gray-600 dark:text-gray-400">
                      Action
                    </th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-gray-200 dark:divide-gray-700">
                  <tr v-for="file in uploads.slice(0, 5)" :key="file.id" class="hover:bg-gray-50/50 dark:hover:bg-gray-800/50 transition">
                    <td class="px-4 py-3 text-xs text-gray-600 dark:text-gray-400 font-mono truncate" :title="file.id">
                      {{ file.id }}
                    </td>
                    <td class="px-4 py-3 text-sm text-gray-900 dark:text-gray-100 truncate">
                      {{ file.original_filename }}
                    </td>
                    <td class="px-4 py-3 text-sm">
                      <span :class="['px-2 py-1 rounded-full text-xs font-medium', getStatusBadgeClass(file.status)]">
                        {{ formatStatus(file.status) }}
                      </span>
                    </td>
                    <td class="px-4 py-3 text-sm text-gray-600 dark:text-gray-400">
                      {{ formatDate(file.created_at) }}
                    </td>
                    <td class="px-4 py-3 text-right">
                      <NuxtLink
                        :to="`/uploads?id=${file.id}`"
                        class="text-xs font-medium text-primary-600 hover:text-primary-700 dark:text-primary-400 dark:hover:text-primary-300 transition"
                      >
                        View
                      </NuxtLink>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-if="uploads.length > 5" class="border-t border-gray-200 dark:border-gray-700 px-4 py-3 bg-gray-50/50 dark:bg-gray-800/50 text-center">
              <NuxtLink
                to="/uploads"
                class="text-xs font-medium text-primary-600 hover:text-primary-700 dark:text-primary-400 dark:hover:text-primary-300 transition"
              >
                View all {{ uploads.length }} uploads →
              </NuxtLink>
            </div>
          </div>
        </div>
      </div>

      <!-- Loading State -->
      <div v-else-if="loading" class="glass rounded-2xl p-6">
        <div class="flex items-center justify-center gap-2 text-sm text-gray-600 dark:text-gray-400">
          <span class="inline-block animate-spin">⟳</span>
          Loading metrics...
        </div>
      </div>

      <!-- Error State -->
      <div v-else-if="error" class="glass rounded-2xl p-6">
        <p class="text-sm text-red-600 dark:text-red-400">
          Failed to load extraction metrics
        </p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { BookOpen, Brain, Sparkles, FileText, CheckCircle2, Clock, AlertCircle } from 'lucide-vue-next'
import { storeToRefs } from 'pinia'

interface FileUpload {
  id: string
  status: 'initiated' | 'processing' | 'completed' | 'failed'
  original_filename: string
  created_at: string
  updated_at: string
}

const userStore = useUserStore()
const { isLoggedIn } = storeToRefs(userStore)

const highlights = ref(['Feature 1', 'Feature 2', 'Feature 3'])
const uploads = ref<FileUpload[]>([])
const loading = ref(false)
const error = ref(false)

definePageMeta({
  layout: 'default',
  middleware: 'auth'
})

// Computed metrics
const completedCount = computed(() => uploads.value.filter(u => u.status === 'completed').length)
const processingCount = computed(() => uploads.value.filter(u => u.status === 'initiated' || u.status === 'processing').length)
const failedCount = computed(() => uploads.value.filter(u => u.status === 'failed').length)
const successRate = computed(() => {
  if (uploads.value.length === 0) return 0
  return Math.round((completedCount.value / uploads.value.length) * 100)
})

async function loadUploads() {
  loading.value = true
  error.value = false

  try {
    const { getBaseUrl } = useBaseUrl()
    const token = await userStore.getToken()

    uploads.value = await $fetch<FileUpload[]>(`${getBaseUrl()}/api/policy-extract/requests/`, {
      headers: { Authorization: `Bearer ${token}` }
    })
  } catch (err) {
    console.error('Failed to load uploads:', err)
    error.value = true
  } finally {
    loading.value = false
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

onMounted(() => {
  if (!isLoggedIn.value) {
    navigateTo('/login')
  } else {
    loadUploads()
  }
})
</script>
