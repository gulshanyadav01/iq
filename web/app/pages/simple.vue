<template>
  <div class="min-h-screen flex items-center justify-center p-6 bg-gradient-to-br from-gray-50 to-primary-50/30 dark:from-gray-900 dark:to-gray-800">
    <div class="w-full max-w-2xl mx-auto text-center">
      <h1 class="text-3xl md:text-5xl font-bold text-gray-900 dark:text-white mb-4">
        🎉 Success!
      </h1>
      <p class="text-xl text-gray-600 dark:text-gray-300 mb-8">
        Your Nuxt app is working correctly! The <code>&lt;NuxtPage&gt;</code> component is rendering pages as expected.
      </p>
      
      <div class="space-y-4">
        <div class="flex flex-wrap justify-center gap-4">
          <NuxtLink to="/" class="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors">
            Home
          </NuxtLink>
          <NuxtLink to="/ui-reference" class="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors">
            Test Page
          </NuxtLink>
          <NuxtLink to="/account" class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
            Account
          </NuxtLink>
        </div>
        
        <div class="mt-8 p-4 bg-white/50 dark:bg-gray-900/50 rounded-lg border border-gray-200/50 dark:border-gray-700/50">
          <h2 class="text-lg font-semibold text-gray-900 dark:text-white mb-2">
            Navigation Test
          </h2>
          <p class="text-sm text-gray-600 dark:text-gray-400">
            All the links above should work without any console warnings about <code>&lt;NuxtPage&gt;</code>
          </p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
definePageMeta({
  layout: 'minimum'
})

useHead({
  title: 'Simple Test Page - InsurIQ'
})
</script>
