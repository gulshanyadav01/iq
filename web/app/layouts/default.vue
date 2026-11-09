<template>
  <div
    class="noise min-h-screen bg-[#f6f7fb] dark:bg-gray-950"
    style="background-image: radial-gradient(ellipse 70% 50% at 100% 0%, color-mix(in srgb, var(--color-primary-300, #a5b4fc) 20%, transparent) 0%, transparent 55%);"
  >
    <slot />
  </div>
</template>

<script setup lang="ts">
// The page owns the left sidebar (single-module app), so the layout is just the
// tinted background + theme initialisation.
onMounted(() => {
  const savedTheme = localStorage.getItem('theme')
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
  const isDark = savedTheme === 'dark' || (!savedTheme && prefersDark)
  document.documentElement.classList.toggle('dark', isDark)
})
</script>
