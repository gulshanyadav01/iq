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
        <div class="flex items-center gap-2 shrink-0">
          <UInput
            v-model="searchQuery"
            icon="i-lucide-search"
            placeholder="Search form no, filename, ID…"
            class="w-64"
            :loading="loading && !!searchQuery"
            @update:model-value="onSearchInput"
            @keydown.enter="runSearch"
          />
          <UPopover v-model:open="dateOpen">
            <button
              type="button"
              :class="['flex items-center gap-2 border px-4 py-2 rounded-lg text-sm font-medium transition duration-200', hasDateFilter ? 'border-primary-500 text-primary-600 dark:text-primary-400 bg-primary-500/10' : 'border-gray-300 dark:border-gray-700 bg-white/70 dark:bg-gray-900/70 hover:bg-white dark:hover:bg-gray-800 text-gray-700 dark:text-gray-200']"
              title="Filter by submitted date/time"
            >
              <UIcon name="i-lucide-calendar" class="w-4 h-4" />
              <span>{{ hasDateFilter ? 'Date: on' : 'Date' }}</span>
            </button>
            <template #content>
              <div class="p-4 w-72 space-y-3">
                <div class="flex flex-wrap gap-2">
                  <button
                    v-for="p in datePresets"
                    :key="p.days"
                    type="button"
                    class="px-2.5 py-1 rounded-md text-xs font-medium border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-600 dark:text-gray-300 transition"
                    @click="setDatePreset(p.days)"
                  >
                    {{ p.label }}
                  </button>
                </div>
                <div class="space-y-1">
                  <label class="block text-[10px] uppercase tracking-widest text-gray-400">From</label>
                  <input v-model="dateFrom" type="datetime-local" class="w-full">
                </div>
                <div class="space-y-1">
                  <label class="block text-[10px] uppercase tracking-widest text-gray-400">To</label>
                  <input v-model="dateTo" type="datetime-local" class="w-full">
                </div>
                <div class="flex justify-between pt-1">
                  <button
                    type="button"
                    class="text-xs font-medium text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition"
                    @click="clearDateRange"
                  >
                    Clear
                  </button>
                  <button
                    type="button"
                    class="px-3 py-1.5 rounded-lg text-xs font-medium bg-primary-600 hover:bg-primary-700 text-white transition"
                    @click="applyDateRange"
                  >
                    Apply
                  </button>
                </div>
              </div>
            </template>
          </UPopover>
          <button
            class="flex items-center gap-2 border border-gray-300 dark:border-gray-700 bg-white/70 dark:bg-gray-900/70 hover:bg-white dark:hover:bg-gray-800 text-gray-700 dark:text-gray-200 px-4 py-2 rounded-lg text-sm font-medium transition duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
            :disabled="loading"
            :title="loading ? 'Refreshing…' : 'Refresh'"
            @click="loadRequests"
          >
            <UIcon name="i-lucide-refresh-cw" :class="['w-4 h-4', loading && 'animate-spin']" />
            <span>Refresh</span>
          </button>
          <button
            class="bg-primary-600 hover:bg-primary-700 text-white px-4 py-2 rounded-lg text-sm font-medium transition duration-200"
            @click="isModalOpen = true"
          >
            New Request
          </button>
        </div>
      </div>

      <!-- Metric Cards (double as status filters) -->
      <div v-if="!loading" class="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <button
          type="button"
          :class="['glass rounded-2xl p-5 space-y-1 text-left transition duration-200 hover:ring-1 hover:ring-primary-300', statusFilter === 'all' && 'ring-2 ring-primary-500']"
          :aria-pressed="statusFilter === 'all'"
          @click="setStatus('all')"
        >
          <p class="text-[10px] uppercase tracking-widest text-gray-400 dark:text-gray-500">Total</p>
          <p class="text-2xl font-semibold text-gray-900 dark:text-white">{{ metrics.total }}</p>
          <p class="text-xs text-gray-400">{{ countsScopeLabel }}</p>
        </button>
        <button
          type="button"
          :class="['glass rounded-2xl p-5 space-y-1 text-left transition duration-200 hover:ring-1 hover:ring-primary-300', statusFilter === 'completed' && 'ring-2 ring-primary-500']"
          :aria-pressed="statusFilter === 'completed'"
          @click="setStatus('completed')"
        >
          <p class="text-[10px] uppercase tracking-widest text-green-500">Completed</p>
          <p class="text-2xl font-semibold text-green-600 dark:text-green-400">{{ metrics.completed }}</p>
          <p class="text-xs text-gray-400">successfully extracted</p>
        </button>
        <button
          type="button"
          :class="['glass rounded-2xl p-5 space-y-1 text-left transition duration-200 hover:ring-1 hover:ring-primary-300', statusFilter === 'failed' && 'ring-2 ring-primary-500']"
          :aria-pressed="statusFilter === 'failed'"
          @click="setStatus('failed')"
        >
          <p class="text-[10px] uppercase tracking-widest text-red-500">Failed</p>
          <p class="text-2xl font-semibold text-red-600 dark:text-red-400">{{ metrics.failed }}</p>
          <p class="text-xs text-gray-400">need attention</p>
        </button>
        <button
          type="button"
          :class="['glass rounded-2xl p-5 space-y-1 text-left transition duration-200 hover:ring-1 hover:ring-primary-300', statusFilter === 'in_progress' && 'ring-2 ring-primary-500']"
          :aria-pressed="statusFilter === 'in_progress'"
          @click="setStatus('in_progress')"
        >
          <p class="text-[10px] uppercase tracking-widest text-blue-500">In Progress</p>
          <p class="text-2xl font-semibold text-blue-600 dark:text-blue-400">{{ metrics.inProgress }}</p>
          <p class="text-xs text-gray-400">processing now</p>
        </button>
      </div>
      <div v-else class="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <div v-for="i in 4" :key="i" class="glass rounded-2xl p-5 space-y-2 animate-pulse">
          <div class="h-2 bg-gray-200 dark:bg-gray-700 rounded w-16" />
          <div class="h-7 bg-gray-200 dark:bg-gray-700 rounded w-10" />
          <div class="h-2 bg-gray-200 dark:bg-gray-700 rounded w-24" />
        </div>
      </div>

      <!-- Failure reasons (buckets) — clickable, each filters the list below to
           that error category. Shown only when there are failures to explain. -->
      <div v-if="!loading && bucketList.length" class="glass rounded-2xl p-5 space-y-3">
        <p class="text-[10px] uppercase tracking-widest text-gray-400 dark:text-gray-500">Failure reasons</p>
        <div class="flex flex-wrap gap-2">
          <button
            v-for="b in bucketList"
            :key="b.key"
            type="button"
            :class="['flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium transition duration-200', categoryFilter === b.key ? 'border-red-500 text-red-600 dark:text-red-400 bg-red-500/10' : 'border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-200 hover:ring-1 hover:ring-primary-300']"
            :aria-pressed="categoryFilter === b.key"
            @click="setCategory(b.key)"
          >
            <span>{{ b.label }}</span>
            <span class="inline-flex items-center justify-center min-w-5 px-1.5 rounded-full bg-gray-200/80 dark:bg-gray-700/80 text-gray-700 dark:text-gray-200 tabular-nums">{{ b.count }}</span>
          </button>
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
        <div v-if="searchQuery.trim() || statusFilter !== 'all' || hasDateFilter || categoryFilter" class="text-center space-y-3">
          <p class="text-sm text-gray-600 dark:text-gray-400">
            No requests match the current filters.
          </p>
          <button
            class="text-xs font-medium text-primary-600 hover:text-primary-700 dark:text-primary-400 dark:hover:text-primary-300 transition"
            @click="clearFilters"
          >
            Clear filters
          </button>
        </div>
        <div v-else class="text-center space-y-3">
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
                  <button type="button" class="flex items-center gap-1 uppercase tracking-widest hover:text-gray-900 dark:hover:text-gray-200 transition" @click="toggleSort('original_filename')">
                    Filename
                    <UIcon :name="sortIcon('original_filename')" :class="['w-3.5 h-3.5', sortField === 'original_filename' ? 'text-primary-500' : 'text-gray-400']" />
                  </button>
                </th>
                <th class="text-left px-4 py-3 text-xs font-semibold uppercase tracking-widest text-gray-600 dark:text-gray-400">
                  Form No.
                </th>
                <th class="text-left px-4 py-3 text-xs font-semibold uppercase tracking-widest text-gray-600 dark:text-gray-400">
                  <button type="button" class="flex items-center gap-1 uppercase tracking-widest hover:text-gray-900 dark:hover:text-gray-200 transition" @click="toggleSort('status')">
                    Status
                    <UIcon :name="sortIcon('status')" :class="['w-3.5 h-3.5', sortField === 'status' ? 'text-primary-500' : 'text-gray-400']" />
                  </button>
                </th>
                <th class="text-left px-4 py-3 text-xs font-semibold uppercase tracking-widest text-gray-600 dark:text-gray-400">
                  <button type="button" class="flex items-center gap-1 uppercase tracking-widest hover:text-gray-900 dark:hover:text-gray-200 transition" @click="toggleSort('created_at')">
                    Submitted
                    <UIcon :name="sortIcon('created_at')" :class="['w-3.5 h-3.5', sortField === 'created_at' ? 'text-primary-500' : 'text-gray-400']" />
                  </button>
                </th>
                <th class="text-left px-4 py-3 text-xs font-semibold uppercase tracking-widest text-gray-600 dark:text-gray-400">
                  <button type="button" class="flex items-center gap-1 uppercase tracking-widest hover:text-gray-900 dark:hover:text-gray-200 transition" @click="toggleSort('updated_at')">
                    Updated
                    <UIcon :name="sortIcon('updated_at')" :class="['w-3.5 h-3.5', sortField === 'updated_at' ? 'text-primary-500' : 'text-gray-400']" />
                  </button>
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
                <td class="px-4 py-3 text-xs text-gray-600 dark:text-gray-400 font-mono" :title="formNum(req)">
                  {{ formNum(req) || '—' }}
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
                  <div class="flex items-center justify-end gap-1">
                    <button
                      class="p-1.5 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition disabled:opacity-40 disabled:cursor-not-allowed"
                      title="Download document"
                      :disabled="downloadingIds.has(req.id)"
                      @click="downloadDocument(req)"
                    >
                      <UIcon name="i-lucide-download" class="w-4 h-4" :class="{ 'animate-pulse': downloadingIds.has(req.id) }" />
                    </button>
                    <button
                      class="p-1.5 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition disabled:opacity-40 disabled:cursor-not-allowed"
                      title="Retry extraction"
                      :disabled="retryingIds.has(req.id)"
                      @click="retryRequest(req.id)"
                    >
                      <UIcon name="i-lucide-refresh-cw" class="w-4 h-4" :class="{ 'animate-spin': retryingIds.has(req.id) }" />
                    </button>
                    <button
                      class="p-1.5 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition"
                      title="View result"
                      @click="openDetail(req)"
                    >
                      <UIcon name="i-lucide-eye" class="w-4 h-4" />
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="total > pageSize" class="flex items-center justify-between gap-4 px-4 py-3 border-t border-gray-200 dark:border-gray-700">
          <p class="text-xs text-gray-400">{{ total }} total</p>
          <UPagination v-model:page="page" :total="total" :items-per-page="pageSize" />
        </div>
      </div>
    </div>

    <!-- Detail Slide-over -->
    <USlideover v-model:open="detailOpen" :title="selectedRequest?.original_filename ?? 'Details'" side="right" :ui="{ content: 'w-full max-w-[90vw] xl:max-w-6xl' }">
      <template #body>
       <div class="flex h-full">
        <!-- Left: source document -->
        <div class="w-3/5 h-full border-r border-gray-200 dark:border-gray-700">
          <DocumentPreview :url="documentUrl" :filename="selectedRequest?.original_filename ?? ''" :content-type="selectedRequest?.content_type ?? null" />
        </div>

        <!-- Right: extracted response -->
        <div class="w-2/5 h-full overflow-hidden flex flex-col">
        <!-- Actions -->
        <div v-if="selectedRequest" class="px-6 py-3 border-b border-gray-200 dark:border-gray-700 flex justify-end">
          <button
            class="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium bg-primary-600 hover:bg-primary-700 disabled:bg-gray-400 disabled:cursor-not-allowed text-white transition duration-200"
            :disabled="retryingIds.has(selectedRequest.id)"
            @click="retryRequest(selectedRequest.id)"
          >
            <UIcon name="i-lucide-refresh-cw" class="w-4 h-4" :class="{ 'animate-spin': retryingIds.has(selectedRequest.id) }" />
            Retry extraction
          </button>
        </div>
        <!-- In-flight -->
        <div v-if="selectedRequest?.status === 'initiated' || selectedRequest?.status === 'processing'" class="p-6 flex items-center gap-2 text-gray-500">
          <UIcon name="i-lucide-loader-circle" class="animate-spin" />
          Processing…
        </div>

        <!-- Failed -->
        <div v-else-if="selectedRequest?.status === 'failed'" class="p-6 space-y-4">
          <span
            v-if="selectedRequest.error_category"
            class="inline-flex items-center rounded-full border border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-900/20 px-2.5 py-0.5 text-xs font-medium text-red-600 dark:text-red-400"
          >
            {{ bucketLabel(selectedRequest.error_category) }}
          </span>
          <div class="text-red-600 dark:text-red-400 p-4 bg-red-50 dark:bg-red-900/20 rounded text-sm">
            {{ selectedRequest.error || 'Unknown error' }}
          </div>
          <div v-if="formattedCallback">
            <div class="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Callback Response</div>
            <pre class="text-xs bg-gray-100 dark:bg-gray-800 p-4 rounded whitespace-pre-wrap leading-relaxed">{{ formattedCallback }}</pre>
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
                <input v-model="activeFilter" type="radio" :value="f.id" class="accent-primary-500" >
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

          <!-- Callback Response view -->
          <div v-else-if="activeView === 'callback'" class="flex-1 overflow-auto p-6">
            <pre v-if="formattedCallback" class="text-xs bg-gray-100 dark:bg-gray-800 p-4 rounded whitespace-pre-wrap leading-relaxed">{{ formattedCallback }}</pre>
            <div v-else class="text-xs text-gray-400">No callback response yet</div>
          </div>
        </div>
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
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'

interface ExtractionRequest {
  id: string
  status: 'initiated' | 'processing' | 'completed' | 'failed'
  original_filename: string
  content_type: string | null
  metadata: Record<string, unknown> | null
  result: Record<string, unknown> | null
  error: string | null
  error_category: string | null
  callback_response: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

interface StatusCounts {
  total: number
  completed: number
  failed: number
  in_progress: number
}

interface PaginatedRequests {
  count: number
  next: string | null
  previous: string | null
  results: ExtractionRequest[]
  counts: StatusCounts
  // Failure counts keyed by error_category, e.g. { expired_link: 3, timeout: 1 }.
  buckets: Record<string, number>
}

// Display labels for each failure bucket. Keys mirror the backend
// PolicyExtractionRequest.ErrorCategory values.
const BUCKET_LABELS: Record<string, string> = {
  expired_link: 'Expired / invalid link',
  unsupported_file: 'Unsupported file type',
  missing_source: 'Missing source',
  timeout: 'Timed out',
  download_failed: 'Download failed',
  parser_failed: 'Extraction failed',
  other: 'Other',
}

function bucketLabel(key: string): string {
  return BUCKET_LABELS[key] ?? key
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

// Pagination state
const pageSize = 20
const page = ref(1)
const total = ref(0)
const counts = ref<StatusCounts>({ total: 0, completed: 0, failed: 0, in_progress: 0 })
// Failure buckets (from the list response) and the active drill-down category.
const buckets = ref<Record<string, number>>({})
const categoryFilter = ref<string | null>(null)
// Buckets rendered as sorted (desc by count) chips with display labels; any
// unmapped key falls back to itself so a new backend category still shows.
const bucketList = computed(() =>
  Object.entries(buckets.value)
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([key, count]) => ({ key, count, label: bucketLabel(key) })),
)

// Search state
const searchQuery = ref('')
let searchDebounce: ReturnType<typeof setTimeout> | undefined
// Monotonic token so an earlier, slower response can't overwrite a newer one
// (rapid typing fires overlapping requests; only the latest may apply).
let loadSeq = 0

// Filter + sort state. Status filter is grouped to match the metric cards
// (in_progress = initiated + processing). Sort fields map to sortable columns.
type StatusFilter = 'all' | 'completed' | 'failed' | 'in_progress'
type SortField = 'created_at' | 'updated_at' | 'original_filename' | 'status'
const statusFilter = ref<StatusFilter>('all')
const sortField = ref<SortField>('created_at')
const sortDir = ref<'asc' | 'desc'>('desc')

// Submitted date/time range (bound to <input type="datetime-local">, i.e. local
// wall-clock strings like "2026-09-03T14:30"). Sent to the API as UTC ISO.
const dateFrom = ref('')
const dateTo = ref('')
const dateOpen = ref(false)
const hasDateFilter = computed(() => !!dateFrom.value || !!dateTo.value)
const datePresets: { label: string; days: number }[] = [
  { label: 'Last 24h', days: 1 },
  { label: '7 days', days: 7 },
  { label: '30 days', days: 30 },
]
// Counts move with search + date range (not the status selection), so the Total
// card's sublabel reflects whether those narrowing filters are active.
const countsScopeLabel = computed(() =>
  searchQuery.value.trim() || hasDateFilter.value ? 'matching filters' : 'all time',
)

// Any filter/sort change returns to page 1. Setting page to 1 triggers the
// page watcher's reload; if already on page 1, reload directly.
function applyAndReload() {
  if (searchDebounce) {
    clearTimeout(searchDebounce)
    searchDebounce = undefined
  }
  if (page.value !== 1) {
    page.value = 1
  } else {
    loadRequests()
  }
}

function setStatus(s: StatusFilter) {
  // Clicking the active status card again toggles the filter off (Total = all).
  statusFilter.value = statusFilter.value === s ? 'all' : s
  applyAndReload()
}

function setCategory(key: string) {
  // Clicking the active bucket again clears the drill-down. Only failed rows
  // carry a category, so the filter already narrows to failures — no need to
  // also touch the status cards.
  categoryFilter.value = categoryFilter.value === key ? null : key
  applyAndReload()
}

function toggleSort(field: SortField) {
  if (sortField.value === field) {
    sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortField.value = field
    // Dates read newest-first by default; text sorts A→Z.
    sortDir.value = field === 'original_filename' || field === 'status' ? 'asc' : 'desc'
  }
  applyAndReload()
}

function sortIcon(field: SortField): string {
  if (sortField.value !== field) return 'i-lucide-chevrons-up-down'
  return sortDir.value === 'asc' ? 'i-lucide-arrow-up' : 'i-lucide-arrow-down'
}

// datetime-local (local wall-clock) → UTC ISO for the API. Returns null for an
// empty or unparsable value so a malformed input can't throw a RangeError from
// toISOString() and wedge the list into its error state.
function toApiIso(local: string): string | null {
  if (!local) return null
  const d = new Date(local)
  return Number.isNaN(d.getTime()) ? null : d.toISOString()
}

// Format a Date to the local "YYYY-MM-DDTHH:mm" value a datetime-local input wants.
function toLocalInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const MS_PER_DAY = 86_400_000

function setDatePreset(days: number) {
  const from = new Date(Date.now() - days * MS_PER_DAY)
  dateFrom.value = toLocalInput(from)
  dateTo.value = ''
  applyAndReload()
}

function applyDateRange() {
  dateOpen.value = false
  applyAndReload()
}

function clearDateRange() {
  dateFrom.value = ''
  dateTo.value = ''
  dateOpen.value = false
  applyAndReload()
}

function formNum(req: ExtractionRequest): string {
  const value = req.metadata?.form_num
  return typeof value === 'string' || typeof value === 'number' ? String(value) : ''
}

function runSearch() {
  // Reset to first page for a new search (applyAndReload also cancels any
  // pending debounce so an Enter press doesn't also fire the timer).
  applyAndReload()
}

function onSearchInput() {
  if (searchDebounce) clearTimeout(searchDebounce)
  searchDebounce = setTimeout(runSearch, 300)
}

function clearFilters() {
  searchQuery.value = ''
  statusFilter.value = 'all'
  categoryFilter.value = null
  dateFrom.value = ''
  dateTo.value = ''
  applyAndReload()
}

const metrics = computed(() => ({
  total: counts.value.total,
  completed: counts.value.completed,
  failed: counts.value.failed,
  inProgress: counts.value.in_progress,
}))

// Modal state
const isModalOpen = ref(false)
const selectedFile = ref<File | null>(null)
const uploading = ref(false)
const uploadError = ref('')

// Detail slide-over state
const selectedRequest = ref<ExtractionRequest | null>(null)
const detailOpen = ref(false)
const documentUrl = ref<string | null>(null)

// Clear the document viewer when the slide-over closes so PDF.js tears down.
watch(detailOpen, (open) => {
  if (!open) documentUrl.value = null
})

const views: { id: 'fields' | 'json' | 'callback'; label: string }[] = [
  { id: 'fields', label: 'Extracted Fields' },
  { id: 'json', label: 'JSON' },
  { id: 'callback', label: 'Callback Response' },
]
const activeView = ref<'fields' | 'json' | 'callback'>('fields')

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
  const { intermediate_files, ...displayResult } = selectedRequest.value.result as Record<string, unknown>
  return JSON.stringify(displayResult, null, 2)
})

const formattedCallback = computed(() => {
  if (!selectedRequest.value?.callback_response) return ''
  return JSON.stringify(selectedRequest.value.callback_response, null, 2)
})

async function getAuthHeaders() {
  const token = await userStore.getToken()
  return { Authorization: `Bearer ${token}` }
}

async function loadRequests() {
  const seq = ++loadSeq
  loading.value = true
  error.value = false

  try {
    const headers = await getAuthHeaders()
    const query: Record<string, string | number> = { page: page.value }
    const search = searchQuery.value.trim()
    if (search) query.search = search
    if (statusFilter.value !== 'all') query.status = statusFilter.value
    if (categoryFilter.value) query.error_category = categoryFilter.value
    // datetime-local values are local wall-clock; convert to UTC ISO for the API.
    const createdAfter = toApiIso(dateFrom.value)
    if (createdAfter) query.created_after = createdAfter
    const createdBefore = toApiIso(dateTo.value)
    if (createdBefore) query.created_before = createdBefore
    query.ordering = (sortDir.value === 'desc' ? '-' : '') + sortField.value
    const data = await $fetch<PaginatedRequests>(`${getBaseUrl()}/api/policy-extract/requests/`, {
      headers,
      query
    })
    // A newer request superseded this one — discard the stale response.
    if (seq !== loadSeq) return
    requests.value = data.results
    total.value = data.count
    counts.value = data.counts
    buckets.value = data.buckets ?? {}
  } catch (err) {
    if (seq !== loadSeq) return
    console.error('Failed to load requests:', err)
    error.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

watch(page, () => loadRequests())

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
    await $fetch<ExtractionRequest>(`${getBaseUrl()}/api/policy-extract/requests/`, {
      method: 'POST',
      headers,
      body: formData
    })

    closeModal()
    if (page.value === 1) {
      await loadRequests()
    } else {
      page.value = 1
    }
  } catch (err) {
    uploadError.value = err instanceof Error ? err.message : 'Upload failed'
  } finally {
    uploading.value = false
  }
}

const retryingIds = ref<Set<string>>(new Set())

// Per-row download of the original uploaded document. Extracted into a composable
// so its success/404/cleanup branches are unit-testable (see the composable's spec).
const { downloadingIds, downloadDocument } = usePolicyExtractDownload()

// The retry buttons render on every row. The backend re-enqueues completed and
// failed requests; it rejects in-flight ones (409) so a retry can't race a
// still-running extraction chain.
async function retryRequest(id: string) {
  retryingIds.value = new Set([...retryingIds.value, id])
  try {
    const headers = await getAuthHeaders()
    const updated = await $fetch<ExtractionRequest>(
      `${getBaseUrl()}/api/policy-extract/requests/${id}/retry/`,
      { method: 'POST', headers },
    )
    if (selectedRequest.value?.id === id) selectedRequest.value = updated
    // Reload to refresh both the row and the status counts (failed → in-progress).
    await loadRequests()
  } catch (err) {
    console.error('Retry failed:', err)
  } finally {
    retryingIds.value = new Set([...retryingIds.value].filter(x => x !== id))
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
  documentUrl.value = `${getBaseUrl()}/api/policy-extract/requests/${req.id}/document/`
  detailOpen.value = true
  activeView.value = 'fields'
  activeFilter.value = 'all'
}

async function copyContent() {
  let text = ''
  if (activeView.value === 'json') {
    text = formattedResult.value
  } else if (activeView.value === 'callback') {
    text = formattedCallback.value
  } else {
    text = filteredFields.value.map(r => `${r.field}\t${r.value ?? ''}`).join('\n')
  }
  await navigator.clipboard.writeText(text)
  copied.value = true
  setTimeout(() => { copied.value = false }, 2000)
}

onMounted(() => {
  loadRequests()
})

onBeforeUnmount(() => {
  if (searchDebounce) clearTimeout(searchDebounce)
})
</script>
