<template>
  <div
    class="fixed top-0 left-0 h-full z-20 w-64 transform transition-transform duration-500 ease-in-out"
    :class="isSidebarOpen ? 'translate-x-0' : '-translate-x-full'"
  >
    <!-- Floating glass panel -->
    <div
class="m-3 h-[calc(100%-1.5rem)] rounded-2xl flex flex-col overflow-hidden
                bg-white/70 dark:bg-gray-950/70
                backdrop-blur-xl
                border border-gray-200/60 dark:border-white/[0.07]
                shadow-xl shadow-black/5 dark:shadow-black/30">

      <!-- Header -->
      <div class="border-b border-gray-200/60 dark:border-white/[0.06]">
        <div class="flex items-center justify-between h-12">
          <NuxtLink to="/home" class="flex items-center">
            <div class="p-1.5 m-2 rounded-lg border border-gray-200/60 dark:border-white/10 bg-white/60 dark:bg-white/5 transition-colors duration-200">
              <img src="/images/icon-light.svg" alt="Logo" class="h-5 dark:hidden" >
              <img src="/images/icon-dark.svg" alt="Logo" class="h-5 hidden dark:block" >
            </div>
          </NuxtLink>
          <button
            class="p-1.5 mr-2 rounded-lg cursor-pointer hover:bg-gray-100 dark:hover:bg-white/[0.06] transition-colors duration-200"
            @click="$emit('update:isSidebarOpen', false)">
            <PanelLeftClose class="h-4 w-4 text-gray-500 dark:text-gray-400" />
          </button>
        </div>
      </div>

      <!-- Nav -->
      <nav class="flex flex-col flex-1 min-h-0 mt-3 overflow-y-auto">
        <div class="px-3 space-y-5">
          <div v-for="section in visibleSections" :key="section.id" class="space-y-1">
            <!-- Section Label -->
            <div v-if="section.label" class="px-2 py-1 text-[10px] font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500">
              {{ section.label }}
            </div>

            <!-- Menu Items -->
            <div class="space-y-0.5">
              <template v-for="item in section.items" :key="item.id">
                <!-- Divider -->
                <div v-if="item.divider" class="my-2 border-t border-gray-200/60 dark:border-white/[0.06]"/>

                <!-- Regular Menu Item -->
                <NuxtLink
v-else-if="item.path && !item.isExternal" :to="item.path"
                  class="flex items-center gap-2 px-2 py-1.5 rounded-lg text-xs font-medium transition-colors duration-200"
                  :class="isActiveMenuItem(item)
                    ? 'bg-primary-500/10 text-primary-700 dark:text-primary-300 border-l-2 border-primary-500'
                    : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100/60 dark:hover:bg-white/[0.04] hover:text-gray-900 dark:hover:text-gray-200'"
                  @click="handleClose">
                  <component :is="item.icon" v-if="item.icon" class="h-4 w-4 flex-shrink-0" />
                  <span>{{ item.label }}</span>
                  <span v-if="item.badge" class="ml-auto px-1.5 py-0.5 text-[10px] rounded-full bg-primary-100 dark:bg-primary-900/40 text-primary-700 dark:text-primary-300">
                    {{ item.badge }}
                  </span>
                </NuxtLink>

                <!-- External Link -->
                <a
v-else-if="item.path && item.isExternal" :href="item.path" target="_blank" rel="noopener noreferrer"
                  class="flex items-center gap-2 px-2 py-1.5 rounded-lg text-xs font-medium transition-colors duration-200 text-gray-600 dark:text-gray-400 hover:bg-gray-100/60 dark:hover:bg-white/[0.04] hover:text-gray-900 dark:hover:text-gray-200"
                  @click="handleClose">
                  <component :is="item.icon" v-if="item.icon" class="h-4 w-4 flex-shrink-0" />
                  <span>{{ item.label }}</span>
                  <ExternalLink class="h-3 w-3 ml-auto opacity-50" />
                </a>

                <!-- Clickable Item (no path) -->
                <button
v-else-if="item.onClick" class="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-xs font-medium transition-colors duration-200 text-gray-600 dark:text-gray-400 hover:bg-gray-100/60 dark:hover:bg-white/[0.04] hover:text-gray-900 dark:hover:text-gray-200"
                  @click="() => { item.onClick?.(); handleClose(); }">
                  <component :is="item.icon" v-if="item.icon" class="h-4 w-4 flex-shrink-0" />
                  <span>{{ item.label }}</span>
                  <span v-if="item.badge" class="ml-auto px-1.5 py-0.5 text-[10px] rounded-full bg-primary-100 dark:bg-primary-900/40 text-primary-700 dark:text-primary-300">
                    {{ item.badge }}
                  </span>
                </button>
              </template>
            </div>
          </div>
        </div>
      </nav>

      <!-- Powered by Rhobots badge -->
      <div class="flex-shrink-0 px-3 pb-3">
        <a href="https://rhobots.ai" target="_blank" rel="noopener noreferrer" class="flex items-center gap-3 px-3 py-2.5 rounded-xl transition-all duration-200
                    bg-gradient-to-br from-gray-100 via-white to-primary-50
                    dark:from-white/[0.06] dark:via-white/[0.03] dark:to-primary-500/[0.08]
                    border border-gray-200/80 dark:border-white/[0.08]
                    hover:from-gray-200/80 hover:via-white hover:to-primary-100/80
                    dark:hover:from-white/[0.09] dark:hover:via-white/[0.05] dark:hover:to-primary-500/[0.13]
                    hover:border-gray-300/80 dark:hover:border-white/[0.13]
                    hover:shadow-sm">
          <img src="/images/icon-light.svg" alt="Rhobots" class="h-7 w-7 flex-shrink-0 dark:hidden">
          <img src="/images/icon-dark.svg" alt="Rhobots" class="h-7 w-7 flex-shrink-0 hidden dark:block">
          <div class="flex flex-col">
            <span class="text-[9px] uppercase tracking-[0.12em] text-gray-400 dark:text-gray-500 font-medium leading-tight">Powered by</span>
            <span class="text-[11px] font-bold tracking-tight text-gray-700 dark:text-gray-200 leading-tight">Rhobots</span>
          </div>
        </a>
      </div>

      <!-- Profile footer -->
      <div v-if="isLoggedIn" class="p-3 border-t border-gray-200/60 dark:border-white/[0.06]">
        <LayoutProfileMenu :is-dark="isDark" @update:is-dark="$emit('update:isDark', $event)" @close="handleClose" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useWindowSize } from '@vueuse/core'
import { PanelLeftClose, ExternalLink } from 'lucide-vue-next'
import { storeToRefs } from 'pinia'
import { computed } from 'vue'
import { useUserStore } from '~/stores/user'
import { useMenu } from '~/composables/useMenu'

defineProps<{
  isSidebarOpen: boolean
  isDark: boolean
  isWhiteLabeled?: boolean
}>()

const userStore = useUserStore()
const { visibleSections, isActiveMenuItem } = useMenu()
const { isLoggedIn } = storeToRefs(userStore)

const emit = defineEmits<{
  (e: 'update:isSidebarOpen', value: boolean): void
  (e: 'update:isDark', value: boolean): void
}>()

const { width } = useWindowSize()
const isMobile = computed(() => width.value < 1024)

const handleClose = () => {
  if (isMobile.value) {
    emit('update:isSidebarOpen', false)
  }
}
</script>

<style scoped>
@media (prefers-contrast: high) {
  .bg-white\/70 {
    background-color: rgba(255, 255, 255, 0.97);
  }
  .dark .bg-gray-950\/70 {
    background-color: rgba(3, 7, 18, 0.97);
  }
}
</style>
