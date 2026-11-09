<template>
  <div>
    <div class="flex min-h-screen">
      <!-- ═══ Left sidebar: brand · upload · status folders · profile ═══ -->
      <aside class="hidden lg:flex flex-col w-64 shrink-0 sticky top-0 h-screen px-4 py-5 bg-white/55 dark:bg-gray-950/40 backdrop-blur-xl border-r border-gray-200/70 dark:border-white/[0.06]">
        <div class="flex items-center gap-2.5 px-2">
          <span class="w-9 h-9 rounded-2xl bg-white dark:bg-gray-900 border border-gray-200/70 dark:border-white/[0.07] shadow-sm flex items-center justify-center">
            <img src="/images/icon-light.svg" alt="InsurIQ" class="h-5 w-5 dark:hidden">
            <img src="/images/icon-dark.svg" alt="InsurIQ" class="h-5 w-5 hidden dark:block">
          </span>
          <span class="text-base font-semibold tracking-tight text-gray-900 dark:text-white">InsurIQ</span>
        </div>

        <button
          class="mt-6 flex items-center justify-center gap-2 bg-gray-900 dark:bg-white text-white dark:text-gray-900 hover:bg-gray-800 dark:hover:bg-gray-100 px-4 py-2.5 rounded-xl text-sm font-medium transition shadow-sm"
          @click="isModalOpen = true"
        >
          <UIcon name="i-lucide-plus" class="w-4 h-4" /> Upload policy
        </button>

        <nav class="mt-6">
          <p class="px-3 mb-1.5 text-[10px] font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500">Policies</p>
          <button
            v-for="n in navLenses"
            :key="n.key"
            type="button"
            :class="['w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-sm transition', n.active
              ? 'bg-gray-900/[0.05] dark:bg-white/[0.07] text-gray-900 dark:text-white font-medium'
              : 'text-gray-500 dark:text-gray-400 hover:bg-gray-900/[0.03] dark:hover:bg-white/[0.04]']"
            @click="n.onClick()"
          >
            <span :class="['w-2 h-2 rounded-full shrink-0', n.dot]" />
            <span class="flex-1 text-left truncate">{{ n.label }}</span>
            <span :class="['text-xs tabular-nums', n.active ? 'text-gray-700 dark:text-gray-200' : 'text-gray-400 dark:text-gray-500']">{{ n.count }}</span>
          </button>
        </nav>

        <div class="flex-1" />

        <div class="flex items-center gap-0.5 p-1 rounded-full bg-gray-100/70 dark:bg-white/[0.04] border border-gray-200/60 dark:border-white/[0.06] w-fit mb-3">
          <button type="button" :class="['w-7 h-7 rounded-full flex items-center justify-center transition', !isDark ? 'bg-white shadow-sm text-gray-900' : 'text-gray-400 hover:text-gray-600']" title="Light mode" @click="setDark(false)"><Sun class="h-3.5 w-3.5" /></button>
          <button type="button" :class="['w-7 h-7 rounded-full flex items-center justify-center transition', isDark ? 'bg-gray-800 text-white' : 'text-gray-400 hover:text-gray-600']" title="Dark mode" @click="setDark(true)"><Moon class="h-3.5 w-3.5" /></button>
        </div>

        <div ref="menuRef" class="relative">
          <button type="button" class="w-full flex items-center gap-2 px-2 py-1.5 rounded-xl bg-gray-100/60 dark:bg-white/[0.04] border border-gray-200/60 dark:border-white/[0.06] hover:bg-gray-200/50 dark:hover:bg-white/[0.07] transition" @click="menuOpen = !menuOpen">
            <span class="h-7 w-7 rounded-full bg-primary-100 dark:bg-primary-900/40 flex items-center justify-center ring-1 ring-gray-200 dark:ring-white/10 overflow-hidden shrink-0">
              <img v-if="profile?.imageUrl" :src="profile.imageUrl" :alt="fullName" class="h-full w-full object-cover">
              <UIcon v-else name="i-lucide-user" class="w-3.5 h-3.5 text-primary-600 dark:text-primary-400" />
            </span>
            <span class="flex-1 text-left text-xs font-medium text-gray-700 dark:text-gray-200 truncate">{{ fullName || 'Account' }}</span>
            <ChevronDown class="h-3.5 w-3.5 text-gray-400 transition" :class="{ 'rotate-180': menuOpen }" />
          </button>
          <div v-if="menuOpen" class="absolute bottom-full left-0 right-0 mb-2 rounded-2xl bg-white dark:bg-gray-900 border border-gray-200/80 dark:border-white/[0.08] shadow-lg overflow-hidden">
            <NuxtLink to="/account" class="flex items-center gap-2 px-3.5 py-2.5 text-xs text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-white/[0.06]" @click="menuOpen = false"><UserCog class="h-3.5 w-3.5" /> Account</NuxtLink>
            <button type="button" class="w-full flex items-center gap-2 px-3.5 py-2.5 text-xs text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20" @click="handleLogout"><LogOut class="h-3.5 w-3.5" /> Logout</button>
          </div>
        </div>
      </aside>

      <!-- ═══ Main ═══ -->
      <main class="flex-1 min-w-0 px-5 lg:px-10 py-6">
        <div class="max-w-6xl mx-auto space-y-6">
      <!-- Heading -->
      <div class="flex items-end justify-between gap-6 flex-wrap">
        <div>
          <h1 class="text-3xl font-bold tracking-tight text-gray-900 dark:text-white">
            Policy Intake
          </h1>
          <p class="text-sm text-gray-500 dark:text-gray-400 mt-1.5">
            AI reads every policy. You only see the ones that need you.
          </p>
        </div>
        <button
          class="lg:hidden flex items-center gap-2 bg-gray-900 dark:bg-white text-white dark:text-gray-900 px-4 py-2 rounded-full text-sm font-medium transition shrink-0"
          @click="isModalOpen = true"
        >
          <UIcon name="i-lucide-plus" class="w-4 h-4" /> Upload
        </button>
      </div>

      <!-- ═══ Overview (wide) + the one thing that needs a human (accent) ═══ -->
      <div v-if="!loading" class="grid grid-cols-1 lg:grid-cols-3 gap-5 items-stretch">
        <!-- Processing overview: gauge + proportional split + legend -->
        <div class="glass rounded-3xl p-6 lg:col-span-2 flex flex-col">
          <div class="flex items-start justify-between gap-3">
            <div>
              <p class="text-sm font-semibold text-gray-900 dark:text-white">Processing overview</p>
              <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">how much the AI handled on its own</p>
            </div>
            <span :class="['inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-medium', straightThroughChip.class]">
              <UIcon name="i-lucide-trending-up" class="w-3 h-3" />
              {{ straightThroughChip.label }}
            </span>
          </div>
          <div class="flex flex-col sm:flex-row sm:items-center gap-7 mt-6 flex-1">
            <!-- Gauge: % inside the ring, label below it (never overflowing) -->
            <div class="shrink-0 flex flex-col items-center gap-2 mx-auto sm:mx-0">
              <div class="relative w-28 h-28">
                <svg viewBox="0 0 36 36" class="w-28 h-28 -rotate-90">
                  <circle cx="18" cy="18" r="15.8" fill="none" stroke="currentColor" stroke-width="2.4" class="text-gray-100 dark:text-gray-800" />
                  <circle
                    cx="18" cy="18" r="15.8" fill="none" stroke="currentColor" stroke-width="2.4"
                    stroke-linecap="round" pathLength="100"
                    :stroke-dasharray="`${metrics.straightThroughPct} 100`"
                    class="text-green-500 transition-all duration-700"
                  />
                </svg>
                <div class="absolute inset-0 flex items-center justify-center">
                  <span class="text-2xl font-bold text-gray-900 dark:text-white tabular-nums">{{ metrics.straightThroughPct }}%</span>
                </div>
              </div>
              <span class="text-[10px] font-medium uppercase tracking-widest text-gray-400 dark:text-gray-500">Straight-through</span>
            </div>
            <!-- Split bar + legend -->
            <div class="flex-1 min-w-0">
              <div class="flex items-baseline gap-2">
                <span class="text-3xl font-bold tracking-tight text-gray-900 dark:text-white tabular-nums">{{ metrics.total }}</span>
                <span class="text-sm text-gray-400 dark:text-gray-500">policies processed</span>
              </div>
              <div class="mt-4 flex gap-1 h-2.5">
                <div v-for="s in splitSegments" :key="s.key" :class="['rounded-full transition-all duration-500', s.bar]" :style="{ width: s.pct + '%' }" :title="`${s.label}: ${s.n}`" />
              </div>
              <div class="mt-4 grid grid-cols-2 gap-x-6 gap-y-2.5">
                <button v-for="s in splitSegments" :key="s.key" type="button" class="flex items-center justify-between gap-2 group" @click="s.onClick()">
                  <span class="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400 group-hover:text-gray-900 dark:group-hover:text-gray-200 transition">
                    <span :class="['w-2 h-2 rounded-full', s.dot]" /> {{ s.label }}
                  </span>
                  <span class="text-xs font-semibold text-gray-700 dark:text-gray-300 tabular-nums">{{ s.n }}</span>
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- Needs review: filled accent card (absorbs the review queue) -->
        <div :class="['rounded-3xl p-6 flex flex-col text-white shadow-sm', metrics.needsReview > 0 ? 'bg-gradient-to-br from-blue-500 to-indigo-600' : 'bg-gradient-to-br from-emerald-400 to-green-500']">
          <div class="flex items-start justify-between">
            <div>
              <p class="text-sm font-semibold">{{ metrics.needsReview > 0 ? 'Needs your review' : 'All caught up' }}</p>
              <p class="text-xs text-white/70 mt-0.5">{{ metrics.needsReview > 0 ? 'awaiting your sign-off' : 'nothing is waiting on you' }}</p>
            </div>
            <span class="w-9 h-9 rounded-xl bg-white/20 flex items-center justify-center shrink-0">
              <UIcon :name="metrics.needsReview > 0 ? 'i-lucide-flag' : 'i-lucide-check'" class="w-4.5 h-4.5" />
            </span>
          </div>
          <div class="mt-4 space-y-2 flex-1">
            <button
              v-for="req in reviewQueue.slice(0, 3)"
              :key="req.id"
              type="button"
              class="w-full flex items-center justify-between gap-2 px-3 py-2 rounded-xl bg-white/15 hover:bg-white/25 transition text-left"
              @click="openDetail(req)"
            >
              <span class="text-xs font-medium truncate">{{ docType(req) || req.original_filename }}</span>
              <span class="text-[11px] text-white/85 shrink-0 tabular-nums">{{ flaggedCountFor(req) }} flagged</span>
            </button>
            <p v-if="!reviewQueue.length" class="text-sm text-white/85 py-3">Every policy has been verified — nothing needs you right now.</p>
          </div>
          <div class="mt-4 flex items-end justify-between">
            <span class="text-4xl font-bold tracking-tight tabular-nums">{{ metrics.needsReview }}</span>
            <button
              type="button"
              class="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-full bg-white text-sm font-semibold transition hover:bg-white/90"
              :class="metrics.needsReview > 0 ? 'text-blue-600' : 'text-green-600'"
              @click="metrics.needsReview > 0 ? setReview('needs_review') : setReview('approved')"
            >
              {{ metrics.needsReview > 0 ? 'Review all' : 'View reviewed' }}
              <UIcon name="i-lucide-arrow-right" class="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>
      <div v-else class="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <div class="glass rounded-3xl p-6 h-52 lg:col-span-2 animate-pulse">
          <div class="h-3 bg-gray-200 dark:bg-gray-700 rounded w-32" />
          <div class="flex gap-7 mt-6">
            <div class="w-32 h-32 rounded-full bg-gray-200 dark:bg-gray-700 shrink-0" />
            <div class="flex-1 space-y-3 pt-5"><div class="h-3 bg-gray-200 dark:bg-gray-700 rounded" /><div class="h-2.5 bg-gray-200 dark:bg-gray-700 rounded w-2/3" /><div class="h-2.5 bg-gray-200 dark:bg-gray-700 rounded w-1/2" /></div>
          </div>
        </div>
        <div class="rounded-3xl p-6 h-52 bg-gray-200 dark:bg-gray-800 animate-pulse" />
      </div>

      <!-- ═══ Policies: full list with explicit lenses ═══ -->
      <div class="space-y-3">
        <div class="flex items-center justify-between gap-4 flex-wrap">
          <div class="flex items-center gap-3 flex-wrap">
            <h2 class="text-base font-semibold text-gray-900 dark:text-white">{{ activeLensLabel }}</h2>
            <span class="text-xs text-gray-400 dark:text-gray-500 tabular-nums">{{ total }} shown</span>
            <!-- Mobile-only lens chips (desktop navigates via the sidebar) -->
            <div class="flex lg:hidden items-center gap-1.5 flex-wrap">
              <button
                v-for="lens in navLenses"
                :key="lens.key"
                type="button"
                :class="['px-3 py-1 rounded-full text-xs font-medium transition duration-200', lens.active
                  ? 'bg-gray-900 text-white dark:bg-white dark:text-gray-900'
                  : 'border border-gray-200 dark:border-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800']"
                :aria-pressed="lens.active"
                @click="lens.onClick()"
              >
                {{ lens.label }}
              </button>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <UInput
              v-model="searchQuery"
              icon="i-lucide-search"
              placeholder="Search policies…"
              class="w-56"
              :ui="{ base: 'rounded-full' }"
              :loading="loading && !!searchQuery"
              @update:model-value="onSearchInput"
              @keydown.enter="runSearch"
            />
            <UPopover v-model:open="dateOpen">
              <button
                type="button"
                :class="['p-2 border rounded-lg transition duration-200', hasDateFilter ? 'border-primary-500 text-primary-600 dark:text-primary-400 bg-primary-500/10' : 'border-gray-200 dark:border-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800']"
                title="Filter by received date"
              >
                <UIcon name="i-lucide-calendar" class="w-4 h-4" />
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
              class="p-2 border border-gray-200 dark:border-gray-700 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition duration-200 disabled:opacity-50"
              :disabled="loading"
              :title="loading ? 'Refreshing…' : 'Refresh'"
              @click="loadRequests"
            >
              <UIcon name="i-lucide-refresh-cw" :class="['w-4 h-4', loading && 'animate-spin']" />
            </button>
          </div>
        </div>

      <!-- Loading State -->
      <div v-if="loading" class="glass rounded-2xl p-8">
        <div class="flex items-center justify-center gap-2 text-sm text-gray-600 dark:text-gray-400">
          <span class="inline-block animate-spin">⟳</span>
          Loading policies…
        </div>
      </div>

      <!-- Error State -->
      <div v-else-if="error" class="glass rounded-2xl p-8">
        <p class="text-sm text-red-600 dark:text-red-400 text-center">
          Couldn't load policies
        </p>
      </div>

      <!-- Empty State -->
      <div v-else-if="requests.length === 0" class="glass rounded-2xl p-12">
        <div v-if="searchQuery.trim() || statusFilter !== 'all' || hasDateFilter || categoryFilter || reviewFilter" class="text-center space-y-3">
          <p class="text-sm text-gray-600 dark:text-gray-400">
            No policies match the current filters.
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
            No policies yet.
          </p>
          <button
            class="text-xs font-medium text-primary-600 hover:text-primary-700 dark:text-primary-400 dark:hover:text-primary-300 transition"
            @click="isModalOpen = true"
          >
            Upload your first policy →
          </button>
        </div>
      </div>

      <!-- Policy cards: no table, no column headers — each policy is a card row
           carrying business identity (line of business, insurer, policy no.,
           insured name), a status pill, AI confidence, and one clear action. -->
      <div v-else class="glass rounded-3xl overflow-hidden divide-y divide-gray-100 dark:divide-gray-800/70">
        <button
          v-for="req in requests"
          :key="req.id"
          type="button"
          class="w-full px-5 py-4 flex items-center gap-4 text-left hover:bg-gray-50/70 dark:hover:bg-white/[0.03] transition duration-200 group"
          @click="openDetail(req)"
        >
          <div :class="['w-10 h-10 shrink-0 rounded-xl flex items-center justify-center', lobStyle(req).tile]">
            <UIcon :name="lobStyle(req).icon" class="w-5 h-5" />
          </div>
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2.5 min-w-0">
              <p class="text-sm font-medium text-gray-900 dark:text-gray-100 truncate" :title="req.original_filename">
                {{ docType(req) || req.original_filename }}
              </p>
              <span :class="['shrink-0 inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium', rowStatus(req).class]">
                {{ rowStatus(req).label }}
              </span>
            </div>
            <p v-if="rowSubline(req)" class="text-xs text-gray-400 dark:text-gray-500 truncate mt-0.5" :title="req.original_filename">
              {{ rowSubline(req) }}
            </p>
          </div>
          <!-- AI confidence -->
          <div v-if="rowAvgConfidence(req) !== null" class="hidden md:flex items-center gap-2 w-36 shrink-0">
            <div class="flex-1 h-1.5 rounded-full bg-gray-200 dark:bg-gray-700 overflow-hidden">
              <div
                :class="['h-full rounded-full', rowAvgConfidence(req)! >= 0.9 ? 'bg-green-500' : rowAvgConfidence(req)! >= 0.7 ? 'bg-amber-500' : 'bg-red-500']"
                :style="{ width: Math.round(rowAvgConfidence(req)! * 100) + '%' }"
              />
            </div>
            <span class="text-xs tabular-nums text-gray-500 dark:text-gray-400 w-9 text-right">{{ Math.round(rowAvgConfidence(req)! * 100) }}%</span>
          </div>
          <div v-else class="hidden md:block w-36 shrink-0" />
          <span class="hidden sm:block text-xs text-gray-400 dark:text-gray-500 w-16 text-right shrink-0 whitespace-nowrap">
            {{ formatDate(req.created_at) }}
          </span>
          <!-- One clear action, plus power-user icons on hover -->
          <div class="flex items-center gap-1.5 shrink-0" @click.stop>
            <button
              class="p-1.5 rounded-lg text-gray-300 hover:text-gray-600 dark:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700/60 transition opacity-0 group-hover:opacity-100 disabled:opacity-40"
              title="Download original PDF"
              :disabled="downloadingIds.has(req.id)"
              @click="downloadDocument(req)"
            >
              <UIcon name="i-lucide-download" class="w-4 h-4" :class="{ 'animate-pulse': downloadingIds.has(req.id) }" />
            </button>
            <button
              v-if="req.status === 'failed'"
              class="p-1.5 rounded-lg text-gray-300 hover:text-gray-600 dark:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700/60 transition opacity-0 group-hover:opacity-100 disabled:opacity-40"
              title="Re-read document"
              :disabled="retryingIds.has(req.id)"
              @click="retryRequest(req.id)"
            >
              <UIcon name="i-lucide-refresh-cw" class="w-4 h-4" :class="{ 'animate-spin': retryingIds.has(req.id) }" />
            </button>
            <span
              :class="['inline-flex items-center gap-1 pl-3.5 pr-3 py-1.5 rounded-full text-xs font-medium transition', req.review_status === 'needs_review'
                ? 'bg-orange-500 group-hover:bg-orange-600 text-white'
                : 'border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 group-hover:bg-gray-100 dark:group-hover:bg-gray-800']"
              @click="openDetail(req)"
            >
              {{ req.review_status === 'needs_review' ? 'Review' : 'View' }}
              <UIcon name="i-lucide-arrow-right" class="w-3.5 h-3.5" />
            </span>
          </div>
        </button>
        <div v-if="total > pageSize" class="flex items-center justify-between gap-4 px-5 py-3.5 bg-gray-50/40 dark:bg-white/[0.02]">
          <p class="text-xs text-gray-400">{{ total }} polic{{ total === 1 ? 'y' : 'ies' }}</p>
          <UPagination v-model:page="page" :total="total" :items-per-page="pageSize" />
        </div>
      </div>
      </div>
        </div>
      </main>
    </div>

    <!-- Detail Slide-over -->
    <USlideover v-model:open="detailOpen" :title="detailTitle" side="right" :ui="{ content: 'w-full max-w-[90vw] xl:max-w-6xl' }">
      <template #body>
       <div v-if="selectedRequest" class="flex flex-col h-full">
        <!-- ═══ Summary header: identity · overall confidence · actions ═══ -->
        <div class="px-6 py-4 flex items-center justify-between gap-4 border-b border-gray-200 dark:border-gray-700">
          <div class="flex items-center gap-3 min-w-0">
            <div :class="['w-10 h-10 shrink-0 rounded-xl flex items-center justify-center', lobStyle(selectedRequest).tile]">
              <UIcon :name="lobStyle(selectedRequest).icon" class="w-5 h-5" />
            </div>
            <div class="min-w-0">
              <div class="flex items-center gap-2">
                <p class="text-sm font-semibold text-gray-900 dark:text-white truncate">{{ docType(selectedRequest) || selectedRequest.original_filename }}</p>
                <span :class="['shrink-0 inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium', rowStatus(selectedRequest).class]">{{ rowStatus(selectedRequest).label }}</span>
              </div>
              <p v-if="rowSubline(selectedRequest)" class="text-xs text-gray-400 dark:text-gray-500 truncate">{{ rowSubline(selectedRequest) }}</p>
            </div>
          </div>
          <div class="flex items-center gap-4 shrink-0">
            <div v-if="overallConfidence !== null" class="text-right leading-none">
              <p :class="['text-lg font-semibold tabular-nums', overallConfidence >= 90 ? 'text-green-600 dark:text-green-400' : overallConfidence >= 70 ? 'text-amber-600 dark:text-amber-400' : 'text-red-600 dark:text-red-400']">{{ overallConfidence }}%</p>
              <p class="text-[10px] uppercase tracking-widest text-gray-400 mt-1">confidence</p>
            </div>
            <button
              v-if="isNeedsReview"
              class="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg text-sm font-medium bg-amber-600 hover:bg-amber-700 disabled:bg-gray-400 disabled:cursor-not-allowed text-white transition duration-200"
              :disabled="approving"
              @click="approveReview"
            >
              <UIcon :name="approving ? 'i-lucide-loader-circle' : 'i-lucide-check'" class="w-4 h-4" :class="{ 'animate-spin': approving }" />
              Approve
            </button>
            <button
              v-if="selectedRequest.status === 'completed' || selectedRequest.status === 'failed'"
              class="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg text-sm font-medium border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50 disabled:cursor-not-allowed transition duration-200"
              :disabled="retryingIds.has(selectedRequest.id)"
              @click="retryRequest(selectedRequest.id)"
            >
              <UIcon name="i-lucide-refresh-cw" class="w-4 h-4" :class="{ 'animate-spin': retryingIds.has(selectedRequest.id) }" />
              Re-read
            </button>
          </div>
        </div>

        <!-- ═══ Status + privacy strip (completed only) ═══ -->
        <div
          v-if="selectedRequest.status === 'completed' && banner"
          :class="['px-6 py-2.5 flex items-center justify-between gap-3 border-b border-gray-100 dark:border-gray-800', BANNER_TONE_CLASS[banner.tone]]"
        >
          <span class="flex items-center gap-2 text-xs font-medium">
            <UIcon :name="banner.icon" class="w-4 h-4 shrink-0" />
            {{ banner.text }}
          </span>
          <span v-if="piiMasking?.enabled" class="shrink-0 inline-flex items-center gap-1.5 text-xs font-medium opacity-80" :title="piiBadgeText">
            <UIcon name="i-lucide-lock" class="w-3.5 h-3.5" />
            <span class="hidden sm:inline">{{ piiBadgeText }}</span>
          </span>
        </div>

        <!-- ═══ In-flight ═══ -->
        <div v-if="selectedRequest.status === 'initiated' || selectedRequest.status === 'processing'" class="flex-1 flex flex-col items-center justify-center gap-3 text-gray-500">
          <UIcon name="i-lucide-loader-circle" class="w-6 h-6 animate-spin text-primary-500" />
          <p class="text-sm">Reading the document…</p>
        </div>

        <!-- ═══ Failed ═══ -->
        <div v-else-if="selectedRequest.status === 'failed'" class="flex-1 overflow-auto p-6 space-y-4">
          <span
            v-if="selectedRequest.error_category"
            class="inline-flex items-center rounded-full border border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-900/20 px-2.5 py-0.5 text-xs font-medium text-red-600 dark:text-red-400"
          >
            {{ bucketLabel(selectedRequest.error_category) }}
          </span>
          <div class="text-red-600 dark:text-red-400 p-4 bg-red-50 dark:bg-red-900/20 rounded-lg text-sm">
            {{ selectedRequest.error || 'The document could not be read.' }}
          </div>
        </div>

        <!-- ═══ Completed: details (primary) beside the source document ═══ -->
        <div v-else-if="selectedRequest.status === 'completed'" class="flex-1 flex overflow-hidden">
          <!-- Details pane (primary) -->
          <div class="flex flex-col w-[56%] border-r border-gray-200 dark:border-gray-700 overflow-hidden">
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

          <!-- Policy details — grouped into the sections a human reviews -->
          <div v-if="activeView === 'fields'" class="flex flex-col flex-1 overflow-hidden">
            <!-- Filter segmented control -->
            <div class="px-6 py-3 flex items-center gap-1.5 border-b border-gray-100 dark:border-gray-800">
              <button
                v-for="f in fieldFilters"
                :key="f.id"
                type="button"
                :class="['px-2.5 py-1 rounded-full text-xs font-medium transition', activeFilter === f.id
                  ? 'bg-gray-900 text-white dark:bg-white dark:text-gray-900'
                  : 'text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800']"
                @click="activeFilter = f.id"
              >
                {{ f.label }}
              </button>
            </div>
            <!-- Grouped sections -->
            <div class="overflow-auto flex-1">
              <div v-for="group in groupedFields" :key="group.key" class="border-b border-gray-100 dark:border-gray-800 last:border-0">
                <button
                  type="button"
                  class="w-full px-6 py-2.5 flex items-center justify-between sticky top-0 bg-gray-50/90 dark:bg-gray-800/80 backdrop-blur-sm hover:bg-gray-100 dark:hover:bg-gray-800 transition z-10"
                  @click="toggleGroup(group.key)"
                >
                  <span class="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-widest text-gray-500 dark:text-gray-400">
                    {{ group.label }}
                    <span class="normal-case tracking-normal text-gray-400 dark:text-gray-500 font-normal">{{ group.rows.length }}</span>
                  </span>
                  <UIcon :name="collapsedGroups.has(group.key) ? 'i-lucide-chevron-down' : 'i-lucide-chevron-up'" class="w-4 h-4 text-gray-400" />
                </button>
                <table v-show="!collapsedGroups.has(group.key)" class="w-full text-sm">
                  <tbody class="divide-y divide-gray-100 dark:divide-gray-800">
                    <tr v-for="row in group.rows" :key="row.field" :class="row.flagged && !isNeedsReview && 'bg-amber-50/40 dark:bg-amber-900/10'">
                      <td class="pl-6 pr-3 py-2 text-xs text-gray-600 dark:text-gray-400 align-top w-5/12">
                        <div class="flex items-center gap-1.5">
                          <span :title="row.field">{{ fieldLabel(row.field) }}</span>
                          <UIcon
                            v-if="row.doVerification"
                            name="i-lucide-flag"
                            class="w-3.5 h-3.5 text-amber-500 shrink-0"
                            title="Flagged for verification"
                          />
                          <span
                            v-if="row.edited"
                            class="inline-flex items-center gap-0.5 text-[10px] text-gray-400"
                            title="Edited during review"
                          >
                            <UIcon name="i-lucide-pencil" class="w-3 h-3" />edited
                          </span>
                        </div>
                      </td>
                      <td class="px-3 py-2 text-sm text-gray-900 dark:text-gray-100">
                        <UInput
                          v-if="isNeedsReview"
                          :model-value="fieldModel(row.field, row.value).get()"
                          size="xs"
                          class="w-full"
                          @update:model-value="fieldModel(row.field, row.value).set(String($event))"
                        />
                        <span v-else class="block truncate max-w-[220px]" :title="String(row.value)">
                          {{ formatFieldValue(row.field, row.value) }}
                        </span>
                      </td>
                      <td class="pl-3 pr-6 py-2 text-right align-top w-20">
                        <span :class="['inline-flex items-center justify-center rounded-full px-2 py-0.5 text-xs font-medium tabular-nums', confidenceClass(row.confidence)]">
                          {{ Math.round(row.confidence * 100) }}%
                        </span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div v-if="groupedFields.length === 0" class="px-6 py-10 text-center text-xs text-gray-400">
                No details match this filter.
              </div>
            </div>
          </div>

          <!-- JSON view -->
          <div v-else-if="activeView === 'json'" class="flex-1 overflow-auto p-6">
            <pre class="text-xs bg-gray-100 dark:bg-gray-800 p-4 rounded whitespace-pre-wrap leading-relaxed">{{ formattedResult }}</pre>
          </div>

          <!-- Integration (callback) view -->
          <div v-else-if="activeView === 'callback'" class="flex-1 overflow-auto p-6">
            <pre v-if="formattedCallback" class="text-xs bg-gray-100 dark:bg-gray-800 p-4 rounded whitespace-pre-wrap leading-relaxed">{{ formattedCallback }}</pre>
            <div v-else class="text-xs text-gray-400">No downstream integration has consumed this policy yet.</div>
          </div>
          </div>

          <!-- Source document pane (reference) -->
          <div class="flex flex-col w-[44%] bg-gray-50 dark:bg-gray-900/40">
            <div class="px-4 py-2.5 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between shrink-0">
              <span class="text-[11px] font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500">Source document</span>
              <a
                v-if="documentUrl"
                :href="documentUrl"
                target="_blank"
                rel="noopener"
                class="inline-flex items-center gap-1 text-xs font-medium text-primary-600 dark:text-primary-400 hover:underline"
              >
                Open <UIcon name="i-lucide-external-link" class="w-3.5 h-3.5" />
              </a>
            </div>
            <div class="flex-1 overflow-hidden">
              <DocumentPreview :url="documentUrl" :filename="selectedRequest?.original_filename ?? ''" :content-type="selectedRequest?.content_type ?? null" />
            </div>
          </div>
        </div>
       </div>
      </template>
    </USlideover>

    <!-- New Request Modal -->
    <UModal v-model:open="isModalOpen" title="Upload a policy" :dismissible="!uploading">
      <template #body>
        <form class="space-y-4" @submit.prevent="handleUpload">
          <!-- Drag-and-drop zone -->
          <label
            :class="['relative flex flex-col items-center justify-center gap-2 px-6 py-10 rounded-xl border-2 border-dashed cursor-pointer transition duration-200', dragOver
              ? 'border-primary-500 bg-primary-500/5'
              : 'border-gray-300 dark:border-gray-600 hover:border-primary-400 hover:bg-gray-50 dark:hover:bg-gray-800/50', uploading && 'opacity-60 pointer-events-none']"
            @dragover.prevent="dragOver = true"
            @dragleave.prevent="dragOver = false"
            @drop.prevent="handleDrop"
          >
            <input
              type="file"
              accept="application/pdf,.pdf"
              class="sr-only"
              :disabled="uploading"
              @change="handleFileSelect"
            >
            <div class="w-11 h-11 rounded-xl bg-primary-500/10 text-primary-600 dark:text-primary-400 flex items-center justify-center">
              <UIcon :name="selectedFile ? 'i-lucide-file-check' : 'i-lucide-upload-cloud'" class="w-5.5 h-5.5" />
            </div>
            <div v-if="selectedFile" class="text-center">
              <p class="text-sm font-medium text-gray-900 dark:text-gray-100 truncate max-w-[18rem]">{{ selectedFile.name }}</p>
              <p class="text-xs text-gray-400 mt-0.5">{{ formatFileSize(selectedFile.size) }} · click to replace</p>
            </div>
            <div v-else class="text-center">
              <p class="text-sm font-medium text-gray-700 dark:text-gray-300">Drop a policy PDF here</p>
              <p class="text-xs text-gray-400 mt-0.5">or click to browse</p>
            </div>
          </label>
          <p class="text-[11px] text-gray-400 dark:text-gray-500 text-center">PDF only · up to 25 MB / 50 pages</p>

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
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { storeToRefs } from 'pinia'
import { onClickOutside } from '@vueuse/core'
import { Sun, Moon, ChevronDown, LogOut, UserCog } from 'lucide-vue-next'
import type { ExtractedFieldData } from '~/composables/usePolicyExtractReview'

// ─── Sidebar shell: theme toggle + profile (moved out of the layout) ─────────
const isDark = ref(false)
function setDark(value: boolean) {
  isDark.value = value
  document.documentElement.classList.toggle('dark', value)
  localStorage.setItem('theme', value ? 'dark' : 'light')
}
onMounted(() => { isDark.value = document.documentElement.classList.contains('dark') })

const profileStore = useUserStore()
const { profile, fullName } = storeToRefs(profileStore)
const { signOut } = useAuthClient()
const menuOpen = ref(false)
const menuRef = ref<HTMLElement | null>(null)
onClickOutside(menuRef, () => { menuOpen.value = false })
async function handleLogout() {
  menuOpen.value = false
  await signOut()
  profileStore.clearProfile()
  window.location.href = '/'
}

interface PiiMasking {
  enabled: boolean
  masked_count: number
  types: Record<string, number>
}

interface ExtractionRequest {
  id: string
  status: 'initiated' | 'processing' | 'completed' | 'failed'
  original_filename: string
  content_type: string | null
  metadata: Record<string, unknown> | null
  // `result.extracted_fields` is a dict of
  // { value, confidence_score, is_extracted, do_verification, edited?, original_value? }
  // and `result.pii_masking` carries the privacy summary (see PiiMasking).
  result: Record<string, unknown> | null
  error: string | null
  error_category: string | null
  callback_response: Record<string, unknown> | null
  review_status: 'auto_approved' | 'needs_review' | 'approved' | null
  reviewed_at: string | null
  created_at: string
  updated_at: string
}

interface StatusCounts {
  total: number
  completed: number
  failed: number
  in_progress: number
  needs_review: number
  auto_approved: number
  approved: number
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
const counts = ref<StatusCounts>({ total: 0, completed: 0, failed: 0, in_progress: 0, needs_review: 0, auto_approved: 0, approved: 0 })
// Failure buckets (from the list response) and the active drill-down category.
// The category is still honored by the list query and surfaced in the detail
// pane; the executive list no longer renders the per-category chip row.
const buckets = ref<Record<string, number>>({})
const categoryFilter = ref<string | null>(null)

// Search state
const searchQuery = ref('')
let searchDebounce: ReturnType<typeof setTimeout> | undefined
// Monotonic token so an earlier, slower response can't overwrite a newer one
// (rapid typing fires overlapping requests; only the latest may apply).
let loadSeq = 0

// Filter + sort state. Status filter is grouped to match the metric cards
// (in_progress = initiated + processing). Sort fields map to sortable columns.
type StatusFilter = 'all' | 'completed' | 'failed' | 'in_progress'
type ReviewFilter = 'auto_approved' | 'needs_review' | 'approved' | null
type SortField = 'created_at' | 'updated_at' | 'original_filename' | 'status'
const statusFilter = ref<StatusFilter>('all')
// Drill-down on review_status (independent of the status cards — the Needs
// Review card toggles this; `counts` is not scoped by it so it stays switchable).
const reviewFilter = ref<ReviewFilter>(null)
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
  // Clicking the active status card again toggles the filter off (Documents = all).
  statusFilter.value = statusFilter.value === s ? 'all' : s
  // Status and review views are mutually exclusive (one active lens on the list).
  reviewFilter.value = null
  categoryFilter.value = null
  applyAndReload()
}

function setReview(key: Exclude<ReviewFilter, null>) {
  // Clicking the active review card again clears it. A review lens implies the
  // completed set, so clear any status/category narrowing.
  reviewFilter.value = reviewFilter.value === key ? null : key
  statusFilter.value = 'all'
  categoryFilter.value = null
  applyAndReload()
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
  reviewFilter.value = null
  dateFrom.value = ''
  dateTo.value = ''
  applyAndReload()
}

// The "Documents" card is the active (highlighted) view only when no narrowing
// status/review/category filter is applied.
const isAllView = computed(
  () => statusFilter.value === 'all' && !reviewFilter.value && !categoryFilter.value,
)

const metrics = computed(() => {
  const c = counts.value
  // Straight-through rate: of the documents the AI finished, the share it was
  // confident enough to clear without a human. The executive efficiency number.
  const finished = c.auto_approved + c.needs_review + c.approved
  const straightThroughPct = finished > 0 ? Math.round((c.auto_approved / finished) * 100) : 0
  return {
    total: c.total,
    completed: c.completed,
    failed: c.failed,
    inProgress: c.in_progress,
    needsReview: c.needs_review,
    autoApproved: c.auto_approved,
    approved: c.approved,
    straightThroughPct,
  }
})

// Explicit filter lenses above the documents list (one active lens at a time).
// Operational lenses (Processing / Failed) only appear when they have members,
// so the default view stays calm.
// Sidebar navigation: the status lenses as "folders" with live counts + a
// coloured dot. The active one filters the policy list below.
const navLenses = computed(() => {
  const m = metrics.value
  const list = [
    { key: 'all', label: 'All policies', icon: 'i-lucide-layers', count: m.total, dot: 'bg-gray-300 dark:bg-gray-600', active: isAllView.value, onClick: () => setStatus('all') },
    { key: 'auto', label: 'Auto-verified', icon: 'i-lucide-shield-check', count: m.autoApproved, dot: 'bg-green-500', active: reviewFilter.value === 'auto_approved', onClick: () => setReview('auto_approved') },
    { key: 'needs', label: 'Needs review', icon: 'i-lucide-flag', count: m.needsReview, dot: 'bg-amber-500', active: reviewFilter.value === 'needs_review', onClick: () => setReview('needs_review') },
    { key: 'approved', label: 'Reviewed', icon: 'i-lucide-check-circle', count: m.approved, dot: 'bg-blue-500', active: reviewFilter.value === 'approved', onClick: () => setReview('approved') },
  ]
  if (m.inProgress > 0 || statusFilter.value === 'in_progress') {
    list.push({ key: 'processing', label: 'Reading', icon: 'i-lucide-loader', count: m.inProgress, dot: 'bg-indigo-400', active: statusFilter.value === 'in_progress', onClick: () => setStatus('in_progress') })
  }
  if (m.failed > 0 || statusFilter.value === 'failed') {
    list.push({ key: 'failed', label: "Couldn't read", icon: 'i-lucide-file-x', count: m.failed, dot: 'bg-red-400', active: statusFilter.value === 'failed', onClick: () => setStatus('failed') })
  }
  return list
})

// The list heading mirrors the active sidebar folder.
const activeLensLabel = computed(() => navLenses.value.find(n => n.active)?.label ?? 'All policies')

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

// Slide-over heading in business terms: "Private Car — POL/2026/…", falling back
// to the filename only when nothing was extracted.
const detailTitle = computed(() => {
  const req = selectedRequest.value
  if (!req) return 'Policy'
  const type = docType(req)
  const num = fieldValue(req, 'policy_number')
  if (type && num) return `${type} — ${num}`
  return type || req.original_filename
})

const views: { id: 'fields' | 'json' | 'callback'; label: string }[] = [
  { id: 'fields', label: 'Policy details' },
  { id: 'json', label: 'Raw data' },
  { id: 'callback', label: 'Integration' },
]
const activeView = ref<'fields' | 'json' | 'callback'>('fields')

const fieldFilters = [
  { id: 'all', label: 'All' },
  { id: 'extracted', label: 'Captured' },
  { id: 'needs', label: 'Flagged' },
  { id: 'key', label: 'Key details' },
]
// Default to "Captured" so the review pane leads with the details the AI actually
// found, not the ~60 empty backfilled schema fields. "All" is still one click away.
const activeFilter = ref<'all' | 'extracted' | 'needs' | 'key'>('extracted')

const KEY_FIELDS = [
  'policy_number', 'insurer_id', 'plan_type_id', 'policy_term', 'business_type',
  'product_id', 'cust_name', 'mobileNo', 'email', 'cust_address', 'reg_num',
  'premium', 'net_premium', 'sum_insured', 'policy_start_date', 'policy_end_date',
  'VehicleClass', 'fuel_type', 'rto_num', 'rto_city',
]

// Review view-model (pure, unit-tested): confidence chips, flag logic, banner.
const {
  confidenceClass,
  flaggedCount,
  reviewBanner,
  toExtractedRows,
  normalizeConfidence,
  fieldLabel,
  groupRows,
  formatFieldValue,
} = usePolicyExtractReview()

// --- Row display helpers (executive list) ------------------------------------
// Pull a field's value out of result.extracted_fields, tolerating the bare-value
// legacy shape.
function fieldValue(req: ExtractionRequest, field: string): string {
  const fields = req.result?.extracted_fields as Record<string, unknown> | undefined
  const data = fields?.[field]
  const v = data && typeof data === 'object' && 'value' in data ? (data as { value: unknown }).value : data
  return v === null || v === undefined ? '' : String(v)
}

// Human-readable policy line from the extracted product type (what a CXO reads),
// not the raw "CV"/"Car" code or the filename.
const PRODUCT_LABELS: Record<string, string> = {
  'Car': 'Private Car',
  'Two Wheeler': 'Two-Wheeler',
  'CV': 'Commercial Vehicle',
}
function docType(req: ExtractionRequest): string {
  const product = fieldValue(req, 'product_id')
  if (product) return PRODUCT_LABELS[product] ?? product
  return fieldValue(req, 'VehicleClass')
}
function insurerName(req: ExtractionRequest): string {
  return fieldValue(req, 'insurer_id')
}

// Colour-coded icon tile per line of business (pastel category tiles, inspired by
// the reference dashboards). Keeps the list visually scannable at a glance.
function lobStyle(req: ExtractionRequest): { icon: string; tile: string } {
  if (req.status === 'failed') return { icon: 'i-lucide-file-x', tile: 'bg-red-500/10 text-red-600 dark:text-red-400' }
  const product = fieldValue(req, 'product_id')
  if (product === 'Car') return { icon: 'i-lucide-car', tile: 'bg-blue-500/10 text-blue-600 dark:text-blue-400' }
  if (product === 'Two Wheeler') return { icon: 'i-lucide-bike', tile: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' }
  if (product === 'CV') return { icon: 'i-lucide-truck', tile: 'bg-amber-500/10 text-amber-600 dark:text-amber-400' }
  return { icon: 'i-lucide-file-text', tile: 'bg-primary-500/10 text-primary-600 dark:text-primary-400' }
}

// A short qualitative read on the straight-through rate (Ember-style "Fast" chip).
const straightThroughChip = computed(() => {
  const pct = metrics.value.straightThroughPct
  if (pct >= 70) return { label: 'Excellent', class: 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400' }
  if (pct >= 40) return { label: 'Healthy', class: 'bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400' }
  return { label: 'Building up', class: 'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400' }
})

// The processing mix, as proportional segments for the overview bar + legend.
// Only non-zero buckets render, so the bar reads cleanly on any dataset.
const splitSegments = computed(() => {
  const m = metrics.value
  const total = m.total || 1
  return [
    { key: 'auto', label: 'Auto-verified', n: m.autoApproved, bar: 'bg-green-500', dot: 'bg-green-500', onClick: () => setReview('auto_approved') },
    { key: 'needs', label: 'Needs review', n: m.needsReview, bar: 'bg-amber-500', dot: 'bg-amber-500', onClick: () => setReview('needs_review') },
    { key: 'reviewed', label: 'Reviewed', n: m.approved, bar: 'bg-blue-500', dot: 'bg-blue-500', onClick: () => setReview('approved') },
    { key: 'reading', label: 'Reading', n: m.inProgress, bar: 'bg-indigo-400', dot: 'bg-indigo-400', onClick: () => setStatus('in_progress') },
    { key: 'unreadable', label: 'Unreadable', n: m.failed, bar: 'bg-red-400', dot: 'bg-red-400', onClick: () => setStatus('failed') },
  ].map(s => ({ ...s, pct: (s.n / total) * 100 })).filter(s => s.n > 0)
})

// Average confidence across the fields the AI actually extracted (0–1), or null
// when there is nothing to average (non-completed / empty result).
function rowAvgConfidence(req: ExtractionRequest): number | null {
  const fields = req.result?.extracted_fields as Record<string, ExtractedFieldData> | undefined
  if (!fields) return null
  let sum = 0
  let n = 0
  for (const data of Object.values(fields)) {
    if (data && typeof data === 'object' && data.is_extracted === 'TRUE') {
      sum += normalizeConfidence(data.confidence_score)
      n += 1
    }
  }
  return n > 0 ? sum / n : null
}

// Review-aware status: completed rows show their review state (the meaningful
// signal), not the generic "Completed".
function rowStatus(req: ExtractionRequest): { label: string; class: string } {
  const green = 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'
  const amber = 'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400'
  const blue = 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400'
  const red = 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400'
  if (req.status === 'completed') {
    if (req.review_status === 'needs_review') return { label: 'Needs review', class: amber }
    if (req.review_status === 'approved') return { label: 'Reviewed', class: blue }
    if (req.review_status === 'auto_approved') return { label: 'Auto-verified', class: green }
    return { label: 'Processed', class: green }
  }
  if (req.status === 'failed') return { label: "Couldn't read", class: red }
  return { label: 'Reading', class: blue }
}

// Business identity line for a policy card: insurer · policy no. · insured name.
// Empty (card shows no subline) when nothing was extracted yet — the title is
// already the filename in that case, so nothing repeats.
function rowSubline(req: ExtractionRequest): string {
  const parts = [
    insurerName(req),
    fieldValue(req, 'policy_number'),
    fieldValue(req, 'cust_name'),
  ].filter(Boolean)
  return parts.join(' · ')
}

// Flagged-field count for the review rail cards (do_verification or sub-threshold).
function flaggedCountFor(req: ExtractionRequest): number {
  return flaggedCount(
    (req.result?.extracted_fields ?? null) as Record<string, ExtractedFieldData> | null,
  )
}

const rawExtractedFields = computed(
  () => (selectedRequest.value?.result?.extracted_fields ?? null) as Record<string, ExtractedFieldData> | null,
)

const extractedFields = computed(() => toExtractedRows(rawExtractedFields.value))

const filteredFields = computed(() => {
  const all = extractedFields.value
  if (activeFilter.value === 'extracted') {
    return all.filter(r => r.value !== null && r.value !== undefined && r.value !== '' && r.value !== '0' && r.value !== 'NO' && r.value !== 'FALSE')
  }
  if (activeFilter.value === 'needs') {
    return all.filter(r => r.flagged)
  }
  if (activeFilter.value === 'key') {
    return all.filter(r => KEY_FIELDS.includes(r.field))
  }
  return all
})

// Grouped into the review sections (Policy / Customer / Vehicle / Premium /
// Add-ons / Other), in reading order, after the active filter is applied.
const groupedFields = computed(() => groupRows(filteredFields.value))

// Collapsible sections (collapsed keys). Everything open by default.
const collapsedGroups = ref<Set<string>>(new Set())
function toggleGroup(key: string) {
  const next = new Set(collapsedGroups.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  collapsedGroups.value = next
}

// PII privacy summary rendered as a header pill when masking ran.
const piiMasking = computed(
  () => (selectedRequest.value?.result?.pii_masking ?? null) as PiiMasking | null,
)
const piiBadgeText = computed(() => {
  const pii = piiMasking.value
  if (!pii) return ''
  if (!pii.masked_count) return 'No PII detected'
  return `${pii.masked_count} PII value${pii.masked_count === 1 ? '' : 's'} masked before the AI`
})

// A brief celebratory banner right after a sign-off, before settling into the
// steady "Reviewed & approved just now" state.
const justApproved = ref(false)

// Review banner for the completed pane, driven by review_status + flag count.
const banner = computed(() => {
  const req = selectedRequest.value
  if (!req) return null
  if (justApproved.value) {
    return { tone: 'green', icon: 'i-lucide-check-circle', text: 'Signed off — all details confirmed' } as const
  }
  const reviewedLabel = req.reviewed_at ? formatDate(req.reviewed_at) : null
  return reviewBanner(req.review_status, flaggedCount(rawExtractedFields.value), reviewedLabel)
})
// Tone backgrounds for the full-width status strip under the summary header.
const BANNER_TONE_CLASS: Record<string, string> = {
  green: 'bg-green-50/70 dark:bg-green-900/15 text-green-700 dark:text-green-400',
  amber: 'bg-amber-50/70 dark:bg-amber-900/15 text-amber-700 dark:text-amber-400',
  blue: 'bg-blue-50/70 dark:bg-blue-900/15 text-blue-700 dark:text-blue-400',
  red: 'bg-red-50/70 dark:bg-red-900/15 text-red-700 dark:text-red-400',
}

// Overall confidence for the summary header: the average across the fields the
// AI actually extracted, as a 0–100 integer (null when nothing to average).
const overallConfidence = computed(() => {
  const req = selectedRequest.value
  if (!req) return null
  const c = rowAvgConfidence(req)
  return c === null ? null : Math.round(c * 100)
})

// Whether the open request is awaiting sign-off (drives inline editing + Approve).
const isNeedsReview = computed(() => selectedRequest.value?.review_status === 'needs_review')

// Inline edits keyed by field id. Kept separate from selectedRequest.result so a
// poll-driven refresh never clobbers in-progress edits. Seeded per field from
// its current value on first keystroke (see fieldModel's getter/setter).
const edits = ref<Record<string, string>>({})
function fieldModel(field: string, current: unknown) {
  return {
    get: () => (field in edits.value ? edits.value[field] : (current == null ? '' : String(current))),
    set: (v: string) => { edits.value = { ...edits.value, [field]: v } },
  }
}

const approving = ref(false)
async function approveReview() {
  const req = selectedRequest.value
  if (!req || approving.value) return
  approving.value = true
  try {
    // Only send fields the reviewer actually changed from their extracted value.
    const changed: Record<string, string> = {}
    for (const [field, value] of Object.entries(edits.value)) {
      const original = extractedFields.value.find(r => r.field === field)?.value
      if (value !== (original == null ? '' : String(original))) changed[field] = value
    }
    const headers = await getAuthHeaders()
    const updated = await $fetch<ExtractionRequest>(
      `${getBaseUrl()}/api/policy-extract/requests/${req.id}/review/`,
      { method: 'POST', headers, body: { fields: changed, approve: true } },
    )
    selectedRequest.value = updated
    const idx = requests.value.findIndex(r => r.id === updated.id)
    if (idx !== -1) requests.value[idx] = updated
    edits.value = {}
    // Brief success affordance before the pane settles into the approved state.
    justApproved.value = true
    setTimeout(() => { justApproved.value = false }, 2600)
    // Refresh the Needs Review count (needs_review → approved).
    await loadRequests()
  } catch (err) {
    console.error('Approve failed:', err)
  } finally {
    approving.value = false
  }
}

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

// The review rail: the first few needs-review documents, independent of the
// list's current lens/pagination so the queue is always visible.
const reviewQueue = ref<ExtractionRequest[]>([])
async function loadReviewQueue() {
  try {
    const headers = await getAuthHeaders()
    const data = await $fetch<PaginatedRequests>(`${getBaseUrl()}/api/policy-extract/requests/`, {
      headers,
      query: { review_status: 'needs_review', page: 1 },
    })
    reviewQueue.value = data.results.slice(0, 6)
  } catch (err) {
    console.error('Failed to load review queue:', err)
  }
}

async function loadRequests() {
  const seq = ++loadSeq
  loading.value = true
  error.value = false
  // Refresh the rail alongside every list load (upload, approve, poll, filter).
  void loadReviewQueue()

  try {
    const headers = await getAuthHeaders()
    const query: Record<string, string | number> = { page: page.value }
    const search = searchQuery.value.trim()
    if (search) query.search = search
    if (statusFilter.value !== 'all') query.status = statusFilter.value
    if (categoryFilter.value) query.error_category = categoryFilter.value
    if (reviewFilter.value) query.review_status = reviewFilter.value
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
    // Keep an open, in-flight detail pane in sync with its freshly loaded row so
    // it flips from "Processing…" to the result without a manual refresh. Only
    // while still in flight, so a completed pane being reviewed isn't clobbered.
    if (detailOpen.value && selectedRequest.value
      && (selectedRequest.value.status === 'initiated' || selectedRequest.value.status === 'processing')) {
      const fresh = data.results.find(r => r.id === selectedRequest.value!.id)
      if (fresh) selectedRequest.value = fresh
    }
  } catch (err) {
    if (seq !== loadSeq) return
    console.error('Failed to load requests:', err)
    error.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

watch(page, () => loadRequests())

const dragOver = ref(false)
const MAX_UPLOAD_BYTES = 25 * 1024 * 1024

// Validate a picked/dropped file client-side (mirrors the backend precheck) so an
// obviously-wrong file is rejected before any upload round-trip.
function acceptFile(file: File | null | undefined) {
  if (!file) return
  const isPdf = file.type === 'application/pdf' || /\.pdf$/i.test(file.name)
  if (!isPdf) {
    uploadError.value = 'That doesn’t look like a PDF. Please upload a policy PDF.'
    selectedFile.value = null
    return
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    uploadError.value = `That file is ${formatFileSize(file.size)} — the limit is 25 MB.`
    selectedFile.value = null
    return
  }
  uploadError.value = ''
  selectedFile.value = file
}

function handleFileSelect(e: Event) {
  const input = e.target as HTMLInputElement
  acceptFile(input.files?.[0])
}

function handleDrop(e: DragEvent) {
  dragOver.value = false
  acceptFile(e.dataTransfer?.files?.[0])
}

function closeModal() {
  isModalOpen.value = false
  selectedFile.value = null
  uploadError.value = ''
  dragOver.value = false
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
  activeFilter.value = 'extracted'
  collapsedGroups.value = new Set()
  edits.value = {}
  justApproved.value = false
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

// Auto-refresh: while any row is still in flight, re-poll the list so statuses,
// counts and an open in-flight detail pane update without a manual refresh.
let pollTimer: ReturnType<typeof setInterval> | undefined
function pollInFlight() {
  const hasInFlight = requests.value.some(r => r.status === 'initiated' || r.status === 'processing')
  if (hasInFlight) loadRequests()
}

onMounted(() => {
  loadRequests()
  pollTimer = setInterval(pollInFlight, 4000)
})

onBeforeUnmount(() => {
  if (searchDebounce) clearTimeout(searchDebounce)
  if (pollTimer) clearInterval(pollTimer)
})
</script>
