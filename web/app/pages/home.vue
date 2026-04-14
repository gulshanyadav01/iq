<template>
  <div class="p-8">
    <div class="max-w-6xl mx-auto space-y-6">
      <!-- Header -->
      <div class="flex items-center justify-between">
        <div>
          <h1 class="text-xl font-semibold tracking-tight text-gray-900 dark:text-white">Dashboard</h1>
          <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">Policy extraction overview</p>
        </div>
        <NuxtLink
          to="/uploads"
          class="bg-primary-600 hover:bg-primary-700 text-white px-4 py-2 rounded-lg text-sm font-medium transition duration-200"
        >
          + New Request
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

      <!-- Skeleton -->
      <div v-else class="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <div v-for="i in 4" :key="i" class="glass rounded-2xl p-5 space-y-2 animate-pulse">
          <div class="h-2 bg-gray-200 dark:bg-gray-700 rounded w-16" />
          <div class="h-7 bg-gray-200 dark:bg-gray-700 rounded w-10" />
          <div class="h-2 bg-gray-200 dark:bg-gray-700 rounded w-24" />
        </div>
      </div>

      <!-- Second row -->
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <!-- Success Rate -->
        <div class="glass rounded-2xl p-6 space-y-4 sm:col-span-2">
          <div class="flex items-center justify-between">
            <p class="text-sm font-semibold text-gray-700 dark:text-gray-300">Success Rate</p>
            <p class="text-lg font-semibold text-gray-900 dark:text-white">{{ successRate }}%</p>
          </div>

          <!-- Overall bar -->
          <div class="h-3 rounded-full bg-gray-100 dark:bg-gray-800 overflow-hidden flex">
            <div
              class="bg-green-500 transition-all duration-700"
              :style="{ width: metrics.total ? `${(metrics.completed / metrics.total) * 100}%` : '0%' }"
            />
            <div
              class="bg-red-400 transition-all duration-700"
              :style="{ width: metrics.total ? `${(metrics.failed / metrics.total) * 100}%` : '0%' }"
            />
            <div
              class="bg-blue-400 transition-all duration-700"
              :style="{ width: metrics.total ? `${(metrics.inProgress / metrics.total) * 100}%` : '0%' }"
            />
          </div>

          <!-- Legend -->
          <div class="flex gap-5 text-xs text-gray-500 dark:text-gray-400">
            <span class="flex items-center gap-1.5"><span class="w-2.5 h-2.5 rounded-sm bg-green-500 inline-block" /> Completed</span>
            <span class="flex items-center gap-1.5"><span class="w-2.5 h-2.5 rounded-sm bg-red-400 inline-block" /> Failed</span>
            <span class="flex items-center gap-1.5"><span class="w-2.5 h-2.5 rounded-sm bg-blue-400 inline-block" /> In Progress</span>
          </div>

          <!-- Empty state hint -->
          <p v-if="!loading && metrics.total === 0" class="text-xs text-gray-400 text-center pt-2">
            No data yet — upload your first policy to see stats.
          </p>
        </div>

        <!-- Quick Action -->
        <div class="glass rounded-2xl p-6 flex flex-col justify-between gap-4">
          <div class="space-y-1">
            <p class="text-sm font-semibold text-gray-700 dark:text-gray-300">Extract a Policy</p>
            <p class="text-xs text-gray-400 dark:text-gray-500 leading-relaxed">
              Upload a PDF and let the pipeline extract all policy fields automatically.
            </p>
          </div>
          <NuxtLink
            to="/uploads"
            class="w-full text-center bg-primary-600 hover:bg-primary-700 text-white px-4 py-2.5 rounded-lg text-sm font-medium transition duration-200"
          >
            Upload Policy
          </NuxtLink>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'

interface ExtractionRequest {
  id: string
  status: 'initiated' | 'processing' | 'completed' | 'failed'
}

definePageMeta({
  layout: 'default',
  middleware: 'auth'
})

const userStore = useUserStore()
const { getBaseUrl } = useBaseUrl()

const requests = ref<ExtractionRequest[]>([])
const loading = ref(true)

const metrics = computed(() => ({
  total: requests.value.length,
  completed: requests.value.filter(r => r.status === 'completed').length,
  failed: requests.value.filter(r => r.status === 'failed').length,
  inProgress: requests.value.filter(r => r.status === 'initiated' || r.status === 'processing').length,
}))

const successRate = computed(() => {
  if (metrics.value.total === 0) return 0
  return Math.round((metrics.value.completed / metrics.value.total) * 100)
})

async function loadRequests() {
  try {
    const token = await userStore.getToken()
    requests.value = await $fetch<ExtractionRequest[]>(
      `${getBaseUrl()}/api/policy-extract/requests/`,
      { headers: { Authorization: `Bearer ${token}` } }
    )
  } catch {
    // silently fail — dashboard is informational
  } finally {
    loading.value = false
  }
}

onMounted(loadRequests)
</script>
