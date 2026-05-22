<template>
  <div class="noise min-h-screen relative bg-white dark:bg-gray-950 text-gray-900 dark:text-gray-100">
    <header
      class="h-12 sticky top-0 z-30 backdrop-blur-xl bg-white/75 dark:bg-gray-950/75 border-b border-gray-200/60 dark:border-white/[0.07] flex items-center px-4 gap-3"
    >
      <NuxtLink
        to="/demos"
        class="text-sm font-semibold tracking-tight flex items-center gap-2 text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 transition-colors duration-200"
        :aria-label="currentPipeline?.name ? 'Back to demos index' : 'Demos'"
      >
        <Sparkles class="h-4 w-4 text-primary-500" />
        <span v-if="!currentPipeline?.name">AI Capability Demos</span>
      </NuxtLink>

      <ChevronRight
        v-if="currentPipeline?.name"
        class="h-3.5 w-3.5 text-gray-300 dark:text-gray-700"
      />
      <span
        v-if="currentPipeline?.name"
        class="text-sm font-semibold tracking-tight"
      >
        {{ currentPipeline.name }}
      </span>

      <div class="flex-1" />
    </header>

    <main>
      <slot />
    </main>
  </div>
</template>

<script setup lang="ts">
import { ChevronRight, Sparkles } from 'lucide-vue-next'
import { storeToRefs } from 'pinia'

import { usePipelinesStore } from '~/stores/pipelines'

const { currentPipeline } = storeToRefs(usePipelinesStore())
</script>
