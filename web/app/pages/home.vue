<template>
  <div class="p-8">
    <div class="max-w-6xl mx-auto space-y-8">
      <!-- Header -->
      <div>
        <h1 class="text-xl font-semibold tracking-tight text-gray-900 dark:text-white">Dashboard</h1>
        <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">Choose a module to get started</p>
      </div>

      <!-- Module Cards -->
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-6">
        <!-- Policy Extract -->
        <NuxtLink
          to="/policy-extract"
          class="glass rounded-2xl p-6 flex flex-col gap-5 hover:ring-1 hover:ring-primary-500/30 transition group"
        >
          <div class="flex items-start justify-between">
            <div class="w-10 h-10 rounded-xl bg-primary-500/10 dark:bg-primary-500/20 flex items-center justify-center">
              <UIcon name="i-lucide-file-search" class="w-5 h-5 text-primary-600 dark:text-primary-400" />
            </div>
            <UIcon name="i-lucide-arrow-right" class="w-4 h-4 text-gray-300 dark:text-gray-600 group-hover:text-primary-500 transition mt-1" />
          </div>
          <div class="space-y-1.5">
            <h2 class="text-sm font-semibold text-gray-900 dark:text-white">Policy Extract</h2>
            <p class="text-xs text-gray-500 dark:text-gray-400 leading-relaxed">
              Upload policy PDFs and automatically extract structured fields — policy number, insurer, premium, dates, vehicle details, and more.
            </p>
          </div>
        </NuxtLink>

        <!-- Commission Intake -->
        <NuxtLink
          to="/commission-intake"
          class="glass rounded-2xl p-6 flex flex-col gap-5 hover:ring-1 hover:ring-primary-500/30 transition group"
        >
          <div class="flex items-start justify-between">
            <div class="w-10 h-10 rounded-xl bg-indigo-500/10 dark:bg-indigo-500/20 flex items-center justify-center">
              <UIcon name="i-lucide-receipt" class="w-5 h-5 text-indigo-600 dark:text-indigo-400" />
            </div>
            <UIcon name="i-lucide-arrow-right" class="w-4 h-4 text-gray-300 dark:text-gray-600 group-hover:text-primary-500 transition mt-1" />
          </div>
          <div class="space-y-1.5">
            <h2 class="text-sm font-semibold text-gray-900 dark:text-white">Commission Intake</h2>
            <p class="text-xs text-gray-500 dark:text-gray-400 leading-relaxed">
              Upload commission statements (.eml, .pdf, .xlsx) and run the full indexing and extraction pipeline to reconcile commissions.
            </p>
          </div>
        </NuxtLink>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
definePageMeta({
  layout: 'default',
  middleware: 'auth'
})
</script>
