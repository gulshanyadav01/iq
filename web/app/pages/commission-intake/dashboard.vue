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
            Extraction and validation statistics
          </p>
        </div>
        <button
          :disabled="loading"
          class="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-600 dark:text-gray-400 transition disabled:opacity-50 disabled:cursor-not-allowed"
          @click="loadDashboard"
        >
          <UIcon name="i-lucide-refresh-cw" class="w-3.5 h-3.5" :class="{ 'animate-spin': loading }" />
          Refresh
        </button>
      </div>

      <!-- Tab bar -->
      <div class="flex gap-1 border-b border-gray-200 dark:border-gray-800">
        <NuxtLink
          to="/commission-intake"
          class="px-4 py-2 text-xs font-medium border-b-2 transition border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
        >
          Documents
        </NuxtLink>
        <NuxtLink
          to="/commission-intake/dashboard"
          class="px-4 py-2 text-xs font-medium border-b-2 transition border-primary-500 text-primary-600 dark:text-primary-400"
        >
          Dashboard
        </NuxtLink>
      </div>

      <!-- Loading skeleton -->
      <div v-if="loading" class="space-y-6">
        <div class="grid grid-cols-2 sm:grid-cols-5 gap-4">
          <div v-for="i in 5" :key="i" class="glass rounded-2xl p-5 space-y-2 animate-pulse">
            <div class="h-2 bg-gray-200 dark:bg-gray-700 rounded w-16" />
            <div class="h-7 bg-gray-200 dark:bg-gray-700 rounded w-10" />
            <div class="h-2 bg-gray-200 dark:bg-gray-700 rounded w-24" />
          </div>
        </div>
        <div class="glass rounded-2xl p-6 space-y-3 animate-pulse">
          <div class="h-3 bg-gray-200 dark:bg-gray-700 rounded w-32" />
          <div v-for="i in 4" :key="i" class="h-8 bg-gray-200 dark:bg-gray-700 rounded" />
        </div>
      </div>

      <template v-else-if="stats">
        <!-- Document-level stat cards -->
        <div class="grid grid-cols-2 sm:grid-cols-5 gap-4">
          <div class="glass rounded-2xl p-5 space-y-1">
            <p class="text-[10px] uppercase tracking-widest text-gray-400 dark:text-gray-500">Total</p>
            <p class="text-2xl font-semibold text-gray-900 dark:text-white">{{ stats.document_stats.total }}</p>
            <p class="text-xs text-gray-400">all time</p>
          </div>
          <div class="glass rounded-2xl p-5 space-y-1">
            <p class="text-[10px] uppercase tracking-widest text-green-500">Extracted</p>
            <p class="text-2xl font-semibold text-green-600 dark:text-green-400">{{ stats.document_stats.extracted }}</p>
            <p class="text-xs text-gray-400">completed</p>
          </div>
          <div class="glass rounded-2xl p-5 space-y-1">
            <p class="text-[10px] uppercase tracking-widest text-emerald-500">Validated</p>
            <p class="text-2xl font-semibold text-emerald-600 dark:text-emerald-400">{{ stats.document_stats.validated }}</p>
            <p class="text-xs text-gray-400">manually verified</p>
          </div>
          <div class="glass rounded-2xl p-5 space-y-1">
            <p class="text-[10px] uppercase tracking-widest text-blue-500">In Progress</p>
            <p class="text-2xl font-semibold text-blue-600 dark:text-blue-400">{{ stats.document_stats.in_progress }}</p>
            <p class="text-xs text-gray-400">processing now</p>
          </div>
          <div class="glass rounded-2xl p-5 space-y-1">
            <p class="text-[10px] uppercase tracking-widest text-red-500">Failed</p>
            <p class="text-2xl font-semibold text-red-600 dark:text-red-400">{{ stats.document_stats.failed }}</p>
            <p class="text-xs text-gray-400">need attention</p>
          </div>
        </div>

        <!-- Provider table -->
        <div class="glass rounded-2xl overflow-hidden">
          <div class="px-6 py-4 border-b border-gray-100 dark:border-gray-800 flex items-center justify-between">
            <p class="text-sm font-semibold text-gray-900 dark:text-white">By Provider</p>
            <p class="text-xs text-gray-400">{{ stats.provider_stats.length }} provider{{ stats.provider_stats.length !== 1 ? 's' : '' }}</p>
          </div>

          <div v-if="!stats.provider_stats.length" class="px-6 py-10 text-center text-sm text-gray-400">
            No completed documents yet.
          </div>
          <table v-else class="w-full">
            <thead>
              <tr class="text-left border-b border-gray-100 dark:border-gray-800">
                <th class="px-6 py-3 text-[10px] font-semibold uppercase tracking-widest text-gray-400">Provider</th>
                <th class="px-6 py-3 text-[10px] font-semibold uppercase tracking-widest text-gray-400 text-right">Extracted</th>
                <th class="px-6 py-3 text-[10px] font-semibold uppercase tracking-widest text-gray-400 text-right">Validated</th>
                <th class="px-6 py-3 text-[10px] font-semibold uppercase tracking-widest text-gray-400 text-right">Validation %</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100 dark:divide-gray-800">
              <tr
                v-for="row in stats.provider_stats"
                :key="row.provider"
                class="hover:bg-gray-50 dark:hover:bg-white/[0.02] transition"
              >
                <td class="px-6 py-3 text-sm font-medium text-gray-900 dark:text-white">{{ row.provider }}</td>
                <td class="px-6 py-3 text-sm text-right text-gray-600 dark:text-gray-400">{{ row.extracted }}</td>
                <td class="px-6 py-3 text-sm text-right text-emerald-600 dark:text-emerald-400 font-medium">{{ row.validated }}</td>
                <td class="px-6 py-3 text-right">
                  <div class="flex items-center justify-end gap-2">
                    <div class="w-16 h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                      <div
                        class="h-full bg-emerald-500 rounded-full"
                        :style="{ width: `${validationPct(row)}%` }"
                      />
                    </div>
                    <span class="text-xs text-gray-600 dark:text-gray-400 w-8 text-right">{{ validationPct(row) }}%</span>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>

      <!-- Error state -->
      <div v-else-if="error" class="glass rounded-2xl p-8 text-center text-sm text-red-600 dark:text-red-400">
        Failed to load dashboard data. <button class="underline ml-1" @click="loadDashboard">Retry</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'

definePageMeta({ layout: 'default', middleware: 'auth' })

interface DocumentStats {
  total: number
  extracted: number
  validated: number
  in_progress: number
  failed: number
}

interface ProviderStat {
  provider: string
  extracted: number
  validated: number
}

interface DashboardData {
  document_stats: DocumentStats
  provider_stats: ProviderStat[]
}

const userStore = useUserStore()
const { getBaseUrl } = useBaseUrl()

const stats = ref<DashboardData | null>(null)
const loading = ref(false)
const error = ref(false)

function validationPct(row: ProviderStat): number {
  if (!row.extracted) return 0
  return Math.round((row.validated / row.extracted) * 100)
}

async function loadDashboard() {
  loading.value = true
  error.value = false
  try {
    const token = await userStore.getToken()
    stats.value = await $fetch<DashboardData>(
      `${getBaseUrl()}/api/commission-intake/documents/dashboard/`,
      { headers: { Authorization: `Bearer ${token}` } }
    )
  } catch (err) {
    console.error('Failed to load dashboard:', err)
    error.value = true
  } finally {
    loading.value = false
  }
}

onMounted(loadDashboard)
</script>
