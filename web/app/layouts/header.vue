<template>
  <div class="min-h-screen bg-gray-50 dark:bg-gray-900">
    <header class="w-full bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
      <div class="max-w-screen-xl mx-auto px-6 py-4">
        <div class="flex items-center justify-between gap-8">
          <NuxtLink to="/" class="flex items-center">
            <img
                :src="isDark ? '/images/icon-dark.svg' : '/images/icon-light.svg'"
                alt="Logo"
                class="h-8"
            />
          </NuxtLink>
          <div class="flex-1 flex items-center justify-end gap-6">
            <a
                href="https://docs.rhobots.ai"
                target="_blank"
                rel="noopener noreferrer"
                class="text-sm text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-gray-100 transition-colors"
            >
              Documentation
            </a>
            <button
                class="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
                @click="isDark = !isDark"
            >
              <Sun v-if="isDark" class="h-5 w-5 text-gray-500 dark:text-gray-400"/>
              <Moon v-else class="h-5 w-5 text-gray-500 dark:text-gray-400"/>
            </button>
          </div>
        </div>
      </div>
    </header>
    <div class="max-w-screen-xl mx-auto">
      <div class="p-6">
        <slot />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { Sun, Moon } from 'lucide-vue-next'
import {storeToRefs} from "pinia";

const isDark = ref(false)

onMounted(() => {
  const savedTheme = localStorage.getItem('theme')
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches

  isDark.value = savedTheme === 'dark' || (!savedTheme && prefersDark)
  if (isDark.value) {
    document.documentElement.classList.add('dark')
  }
})

watch(isDark, (newValue) => {
  document.documentElement.classList.toggle('dark', newValue)
  localStorage.setItem('theme', newValue ? 'dark' : 'light')
})
</script>