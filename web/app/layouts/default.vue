<template>
  <div
    class="noise min-h-screen relative bg-white dark:bg-gray-950"
    style="background-image: radial-gradient(ellipse 80% 50% at 70% -10%, color-mix(in srgb, var(--color-primary-200, #c7d2fe) 30%, transparent) 0%, transparent 60%);"
  >

    <!-- Sidebar -->
    <LayoutSidebar ref="sideBarRef" :is-sidebar-open="isSidebarOpen" :is-dark="isDark" @update:is-dark="isDark = $event"
      @update:is-sidebar-open="isSidebarOpen = $event" />

    <!-- Floating sidebar toggle (visible when sidebar is closed) -->
    <button
      v-show="!isSidebarOpen"
      class="fixed top-3 left-3 z-30 p-1.5 rounded-lg bg-white/70 dark:bg-gray-950/70 backdrop-blur-xl border border-gray-200/60 dark:border-white/[0.07] shadow-sm hover:bg-white dark:hover:bg-gray-900 transition-colors duration-200"
      @click="isSidebarOpen = true">
      <PanelLeft class="h-4 w-4 text-gray-500 dark:text-gray-400" />
    </button>

    <!-- Main Content -->
    <main class="transition-[padding] duration-500" :class="{ 'lg:pl-[17rem]': isSidebarOpen }">
      <div v-if="isSidebarOpen && isMobile" class="fixed z-10 inset-0 bg-black/25 backdrop-blur-sm"></div>
      <div class="relative z-[1]">
        <slot />
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { onClickOutside, useWindowSize } from "@vueuse/core";
import { PanelLeft } from 'lucide-vue-next';

const { width } = useWindowSize()

const isMobile = computed(() => width.value < 1024)

const isSidebarOpen = ref(false)
const isDark = ref(false)
const sideBarRef = ref<HTMLElement | null>(null)

onMounted(() => {
  // Check for saved theme preference or system preference
  const savedTheme = localStorage.getItem('theme')
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches

  isDark.value = savedTheme === 'dark' || (!savedTheme && prefersDark)
  if (isDark.value) {
    document.documentElement.classList.add('dark')
  } else {
    document.documentElement.classList.remove('dark')
  }

  // Watch for screen size changes
  watch(isMobile, (newValue) => {
    isSidebarOpen.value = !newValue;
  }, { immediate: true })
})

onClickOutside(sideBarRef, () => {
  if (isSidebarOpen.value && isMobile.value) {
    isSidebarOpen.value = false;
  }
})
</script>
