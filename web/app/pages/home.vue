<template>
  <!-- Single-module product: Home IS Policy Intake. The /home route stays alive
       only so the login flow's landing (index.vue → /home) keeps working; the
       inline middleware below forwards it straight to the product. -->
  <div class="min-h-screen flex items-center justify-center">
    <span class="inline-block animate-spin text-gray-400">⟳</span>
  </div>
</template>

<script setup lang="ts">
definePageMeta({
  layout: false,
  middleware: [
    'auth',
    // Canonical redirect: returning navigateTo from route middleware is the
    // one mechanism Nuxt guarantees (an onMounted navigateTo proved flaky here).
    () => navigateTo('/policy-extract', { replace: true }),
  ],
})
</script>
