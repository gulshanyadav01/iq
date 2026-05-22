<template>
  <div class="px-6 py-10 lg:px-10 lg:py-14 max-w-6xl mx-auto">
    <h1 class="text-xl font-semibold tracking-tight mb-2">AI Capability Demos</h1>
    <p class="text-sm text-gray-500 dark:text-gray-400 max-w-2xl">
      Bring your own document. Watch our AI turn it into structured data with
      verifiable citations back to the source.
    </p>

    <div v-if="isLoading" class="mt-10 text-sm text-gray-400">Loading…</div>

    <div
      v-else
      class="mt-10 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6"
    >
      <NuxtLink
        v-for="p in pipelines"
        :key="p.id"
        :to="`/demos/pipelines/${p.slug}`"
        class="group glass-subtle rounded-2xl p-6 hover:shadow-md transition-shadow duration-300 flex flex-col gap-3"
      >
        <div class="flex items-center gap-2">
          <FileText class="h-4 w-4 text-primary-500" />
          <span class="text-[10px] uppercase tracking-widest text-gray-400">
            Demo
          </span>
        </div>
        <h2 class="text-sm font-semibold tracking-tight">{{ p.name }}</h2>
        <p class="text-xs text-gray-500 dark:text-gray-400 flex-1">
          {{ p.description }}
        </p>
        <div class="flex items-center gap-1 text-xs text-primary-600 dark:text-primary-400 group-hover:underline">
          Open demo
          <ArrowRight class="h-3.5 w-3.5" />
        </div>
      </NuxtLink>
    </div>

    <div
      v-if="!isLoading && pipelines.length === 0"
      class="mt-10 text-sm text-gray-400"
    >
      No demos available. Run migrations to seed the starter pipeline.
    </div>
  </div>
</template>

<script setup lang="ts">
import { ArrowRight, FileText } from 'lucide-vue-next'
import { storeToRefs } from 'pinia'
import { usePipelinesStore } from '~/stores/pipelines'

definePageMeta({
  layout: 'demos',
  middleware: 'auth',
})

const store = usePipelinesStore()
const { pipelines } = storeToRefs(store)
const isLoading = ref(true)

onMounted(async () => {
  try {
    await store.loadPipelines()
  } finally {
    isLoading.value = false
  }
})
</script>
