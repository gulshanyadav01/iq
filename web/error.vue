<template>
  <div class="min-h-screen bg-gradient-to-br from-gray-50 to-primary-50/30 dark:from-gray-900 dark:to-gray-800 flex items-center justify-center p-6">
    <div class="max-w-md w-full">
      <!-- Error Card -->
      <div class="relative">
        <div class="absolute inset-0 bg-gradient-to-r from-primary-500 to-purple-500 rounded-2xl opacity-10"></div>
        <div class="relative p-8 rounded-2xl bg-white/50 dark:bg-gray-900/50 backdrop-blur-sm border border-gray-200/50 dark:border-gray-700/50 shadow-xl">
          <!-- Error Icon -->
          <div class="mx-auto w-24 h-24 mb-6">
            <div class="relative">
              <div class="absolute inset-0 rounded-full bg-red-100 dark:bg-red-900/30 animate-ping opacity-50" style="animation-duration: 3s"></div>
              <div class="relative flex items-center justify-center w-24 h-24 rounded-full bg-red-100 dark:bg-red-900/50">
                <AlertTriangle class="h-12 w-12 text-red-600 dark:text-red-400"/>
              </div>
            </div>
          </div>

          <!-- Error Content -->
          <div class="text-center">
            <h1 class="text-3xl font-bold text-gray-900 dark:text-gray-100 mb-2">
              {{ error?.statusCode === 404 ? 'Page Not Found' : 'Something Went Wrong' }}
            </h1>
            <p class="text-gray-600 dark:text-gray-300 mb-6">
              {{ error?.message || 'We couldn\'t find the page you were looking for or an unexpected error occurred.' }}
            </p>

            <!-- Error Code -->
            <div class="inline-block px-3 py-1 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 text-sm font-mono mb-6">
              Error {{ error?.statusCode || 500 }}
            </div>

            <!-- Action Buttons -->
            <div v-if="error?.statusCode !== 412" class="flex flex-col sm:flex-row gap-3 justify-center">
              <button
                  @click="handleError"
                  class="px-4 py-2 rounded-lg bg-primary-600 text-white text-sm font-medium hover:bg-primary-700 transition-colors"
              >
                {{ error?.statusCode === 404 ? 'Go Home' : 'Try Again' }}
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Logo -->
      <div class="mt-8 text-center">
        <NuxtLink to="/" class="inline-block">
          <img
              :src="isDark ? '/images/icon-dark.svg' : '/images/icon-light.svg'"
              alt="Logo"
              class="h-8 mx-auto"
          />
        </NuxtLink>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import {ref, onMounted} from 'vue';
import {AlertTriangle} from 'lucide-vue-next';

definePageMeta({
  layout: 'minimum'
});

const props = defineProps({
  error: Object
});

const isDark = ref(false);

onMounted(() => {
  // Check for saved theme preference or system preference
  const savedTheme = localStorage.getItem('theme');
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;

  isDark.value = savedTheme === 'dark' || (!savedTheme && prefersDark);
});

const handleError = async () => {
  if (props.error?.statusCode === 404) {
    navigateTo('/');
  } else {
    if (props.error?.statusCode === 403) {
      // log out
      await forceLogOut()
    }
    clearError({redirect: '/'});
  }
};

const forceLogOut = async () => {
  const {signOut} = useAuthClient()
  await signOut()

  const userStore = useUserStore()
  userStore.clearProfile()
}
</script>