<template>
  <div v-if="hasError" role="alert" class="p-6 text-center">
    <AlertTriangle class="h-8 w-8 mx-auto text-red-500 mb-2" />
    <p class="text-sm font-medium mb-1">Something went wrong with this run</p>
    <p class="text-xs text-gray-400 mb-4">{{ message }}</p>
    <button
      type="button"
      class="text-xs px-3 py-1.5 rounded-md border border-gray-200 dark:border-white/[0.08] hover:bg-gray-100 dark:hover:bg-gray-900"
      @click="reset"
    >
      Try again
    </button>
  </div>
  <slot v-else />
</template>

<script setup lang="ts">
import { AlertTriangle } from 'lucide-vue-next'

const emit = defineEmits<{ retry: [] }>()
const hasError = ref(false)
const message = ref('')

onErrorCaptured((err) => {
  hasError.value = true
  message.value = (err as Error)?.message ?? 'Unknown error'
  return false
})

function reset() {
  hasError.value = false
  message.value = ''
  emit('retry')
}
</script>
