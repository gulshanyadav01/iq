<template>
  <TransitionRoot appear :show="show" as="template">
    <Dialog as="div" @close="$emit('close')" class="relative z-50">
      <TransitionChild
          as="template"
          enter="duration-300 ease-out"
          enter-from="opacity-0"
          enter-to="opacity-100"
          leave="duration-200 ease-in"
          leave-from="opacity-100"
          leave-to="opacity-0"
      >
        <div class="fixed inset-0 bg-black/25"/>
      </TransitionChild>

      <div class="fixed inset-0 overflow-y-auto">
        <div class="flex min-h-full items-center justify-center p-4">
          <TransitionChild
              as="template"
              enter="duration-300 ease-out"
              enter-from="opacity-0 scale-95"
              enter-to="opacity-100 scale-100"
              leave="duration-200 ease-in"
              leave-from="opacity-100 scale-100"
              leave-to="opacity-0 scale-95"
          >
            <DialogPanel class="w-full max-w-md transform overflow-hidden rounded-lg bg-white dark:bg-gray-900 p-6 shadow-xl transition-all">
              <div class="mx-auto flex h-12 w-12 items-center justify-center rounded-full" :class="typeClasses.iconBg">
                <component :is="typeClasses.icon" class="h-6 w-6" :class="typeClasses.iconColor"/>
              </div>

              <div class="mt-3 text-center">
                <DialogTitle as="h3" class="text-lg font-medium leading-6 text-gray-900 dark:text-gray-100">
                  {{ title }}
                </DialogTitle>

                <div class="mt-2">
                  <p class="text-sm text-gray-500 dark:text-gray-400">
                    {{ message }}
                  </p>
                </div>

                <div class="mt-6">
                  <button
                      class="w-full rounded-lg px-4 py-2 text-sm font-medium transition-colors"
                      :class="typeClasses.buttonClasses"
                      @click="$emit('close')"
                  >
                    {{ buttonText }}
                  </button>
                </div>
              </div>
            </DialogPanel>
          </TransitionChild>
        </div>
      </div>
    </Dialog>
  </TransitionRoot>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Dialog, DialogPanel, DialogTitle, TransitionChild, TransitionRoot } from '@headlessui/vue'
import { CheckCircle2, AlertCircle, AlertTriangle, Info } from 'lucide-vue-next'

const props = defineProps<{
  show: boolean
  type: 'success' | 'error' | 'warning' | 'info'
  title: string
  message: string
  buttonText?: string
}>()

defineEmits<{
  (e: 'close'): void
}>()

const typeClasses = computed(() => {
  switch (props.type) {
    case 'success':
      return {
        icon: CheckCircle2,
        iconBg: 'bg-green-100 dark:bg-green-900/50',
        iconColor: 'text-green-600 dark:text-green-400',
        buttonClasses: 'bg-green-600 text-white hover:bg-green-700'
      }
    case 'error':
      return {
        icon: AlertCircle,
        iconBg: 'bg-red-100 dark:bg-red-900/50',
        iconColor: 'text-red-600 dark:text-red-400',
        buttonClasses: 'bg-red-600 text-white hover:bg-red-700'
      }
    case 'warning':
      return {
        icon: AlertTriangle,
        iconBg: 'bg-amber-100 dark:bg-amber-900/50',
        iconColor: 'text-amber-600 dark:text-amber-400',
        buttonClasses: 'bg-amber-600 text-white hover:bg-amber-700'
      }
    case 'info':
    default:
      return {
        icon: Info,
        iconBg: 'bg-blue-100 dark:bg-blue-900/50',
        iconColor: 'text-blue-600 dark:text-blue-400',
        buttonClasses: 'bg-blue-600 text-white hover:bg-blue-700'
      }
  }
})
</script>