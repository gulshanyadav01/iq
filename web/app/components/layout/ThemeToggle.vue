<template>
  <button
      class="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-gray-600 dark:text-gray-400 hover:bg-gray-100/60 dark:hover:bg-white/[0.06] hover:text-gray-900 dark:hover:text-gray-200 transition-colors duration-200"
      @click="toggleTheme"
  >
    <Sun v-if="isDark" class="h-3.5 w-3.5"/>
    <Moon v-else class="h-3.5 w-3.5"/>
    {{ isDark ? 'Light mode' : 'Dark mode' }}
  </button>
</template>

<script setup lang="ts">
  import { Sun, Moon } from 'lucide-vue-next'

  const props = defineProps<{
    isDark: boolean
  }>()

  const emit = defineEmits<{
    (e: 'update:isDark', value: boolean): void
  }>()

  const toggleTheme = () => {
    const newValue = !props.isDark
    emit('update:isDark', newValue)
    document.documentElement.classList.toggle('dark')
    localStorage.setItem('theme', newValue ? 'dark' : 'light')
  }
</script>