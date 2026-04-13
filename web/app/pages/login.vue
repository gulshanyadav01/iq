<template>
  <div
    class="noise min-h-screen relative overflow-hidden flex items-center justify-center bg-white dark:bg-gray-950"
    style="background-image: radial-gradient(ellipse 80% 50% at 70% -10%, color-mix(in srgb, var(--color-primary-200, #c7d2fe) 60%, transparent) 0%, transparent 60%), radial-gradient(ellipse 60% 40% at 20% 110%, color-mix(in srgb, var(--color-primary-300, #a5b4fc) 40%, transparent) 0%, transparent 55%);"
  >

    <!-- Theme Toggle -->
    <button
      class="absolute top-5 right-5 z-30 p-2 rounded-xl glass text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 transition-colors duration-200"
      @click="toggleTheme"
      :title="isDark ? 'Switch to light mode' : 'Switch to dark mode'">
      <Sun v-if="isDark" class="h-4 w-4" />
      <Moon v-else class="h-4 w-4" />
    </button>

    <!-- Main Content -->
    <div class="relative z-20 w-full max-w-sm mx-auto px-5 py-8 login-content">

      <!-- Logo / Brand -->
      <div class="text-center mb-8">
        <div class="inline-flex items-center justify-center w-12 h-12 glass rounded-2xl mb-5 shadow-lg">
          <NuxtLink to="/home" class="flex items-center">
            <img src="/images/icon-light.svg" alt="Logo" class="h-7 dark:hidden" />
            <img src="/images/icon-dark.svg" alt="Logo" class="h-7 hidden dark:block" />
          </NuxtLink>
        </div>
        <h1 class="text-xl font-semibold tracking-tight text-gray-900 dark:text-white mb-1.5">
          Welcome back
        </h1>
        <p class="text-xs text-gray-500 dark:text-gray-400 font-normal">
          Sign in to continue your journey
        </p>
      </div>

      <!-- Login Card -->
      <div class="glass rounded-2xl shadow-xl shadow-black/5 dark:shadow-black/30 p-6">
        <AuthLogin />
      </div>

      <!-- Bottom Links -->
      <div class="text-center mt-6">
        <p class="text-xs text-gray-400 dark:text-gray-500">
          Don't have an account?
          <a href="#" class="text-gray-700 dark:text-gray-300 font-medium hover:text-gray-900 dark:hover:text-white transition-colors underline underline-offset-2">
            Sign up
          </a>
        </p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Moon, Sun } from 'lucide-vue-next'
import { storeToRefs } from "pinia"

const userStore = useUserStore()
const { isLoggedIn } = storeToRefs(userStore)

const isDark = ref(false)

definePageMeta({
  layout: false
})

const toggleTheme = () => {
  isDark.value = !isDark.value
  if (isDark.value) {
    document.documentElement.classList.add('dark')
  } else {
    document.documentElement.classList.remove('dark')
  }
  localStorage.setItem('theme', isDark.value ? 'dark' : 'light')
}

onMounted(() => {
  const savedTheme = localStorage.getItem('theme')
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches

  isDark.value = savedTheme === 'dark' || (!savedTheme && prefersDark)
  if (isDark.value) {
    document.documentElement.classList.add('dark')
  } else {
    document.documentElement.classList.remove('dark')
  }

  const { setColorPalette } = useTheme()
  const savedPalette = localStorage.getItem('colorPalette')
  if (savedPalette) {
    setColorPalette(savedPalette)
  }

  if (isLoggedIn.value) {
    navigateTo('/home')
  }

  watch(() => isLoggedIn.value, (newIsLoggedIn) => {
    if (newIsLoggedIn) {
      navigateTo('/home')
    }
  })
})
</script>

<style scoped>
/* Entrance animation */
.login-content {
  animation: fade-slide-up 0.5s ease-out both;
}

@keyframes fade-slide-up {
  from { opacity: 0; transform: translateY(16px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* Accessibility */
@media (prefers-reduced-motion: reduce) {
  .login-content {
    animation: none;
  }
}
</style>
