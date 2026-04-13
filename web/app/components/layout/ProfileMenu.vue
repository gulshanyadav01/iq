<template>
  <div class="relative">
    <ClientOnly>
      <button
          ref="menuRef"
          class="w-full flex items-center gap-2 px-2 py-1.5 rounded-xl text-xs font-medium
                 bg-gray-100/60 dark:bg-white/[0.04]
                 border border-gray-200/60 dark:border-white/[0.06]
                 text-gray-700 dark:text-gray-300
                 hover:bg-gray-200/60 dark:hover:bg-white/[0.08]
                 transition-colors duration-200"
          @click="isOpen = !isOpen"
      >
        <div class="relative h-7 w-7 rounded-full overflow-hidden bg-primary-100 dark:bg-primary-900/40 flex items-center justify-center ring-1 ring-gray-200 dark:ring-white/10 flex-shrink-0">
          <img
              v-if="profile?.imageUrl"
              :src="profile.imageUrl"
              :alt="fullName"
              class="h-full w-full object-cover rounded-full"
          />
          <User v-else class="h-3.5 w-3.5 text-primary-600 dark:text-primary-400"/>
        </div>

        <div class="flex-1 text-left min-w-0">
          <div class="font-medium text-gray-800 dark:text-gray-200 truncate">{{ fullName }}</div>
          <div class="inline-flex items-center">
            <span v-if="isPro" class="px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-primary-100 dark:bg-primary-900/40 text-primary-700 dark:text-primary-300 inline-flex items-center gap-1">
              <Sparkles class="h-2.5 w-2.5" />
              Pro
            </span>
            <span v-else class="text-[10px] text-gray-400 dark:text-gray-500">Free</span>
          </div>
        </div>

        <ChevronUp
            class="h-3.5 w-3.5 text-gray-400 transition-transform duration-200 flex-shrink-0"
            :class="{ 'rotate-180': !isOpen }"
        />
      </button>
    </ClientOnly>

    <!-- Dropdown Menu -->
    <div
        v-show="isOpen"
        class="absolute bottom-full left-0 right-0 mb-2 glass rounded-xl shadow-xl shadow-black/5 dark:shadow-black/30 overflow-hidden"
    >
      <div class="py-1 space-y-0.5">
        <!-- Account -->
        <div>
          <NuxtLink
              to="/account"
              class="flex items-center gap-2 px-3 py-1.5 text-xs text-gray-600 dark:text-gray-400 hover:bg-gray-100/60 dark:hover:bg-white/[0.06] hover:text-gray-900 dark:hover:text-gray-200 transition-colors duration-200"
              @click="handleClose"
          >
            <Settings class="h-3.5 w-3.5"/>
            Account
          </NuxtLink>
        </div>

        <!-- Settings + Logout -->
        <div class="border-t border-gray-200/60 dark:border-white/[0.06] pt-1">
          <LayoutThemeToggle :is-dark="isDark" @update:is-dark="$emit('update:isDark', $event)" />
          <button
              class="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-red-500 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/[0.08] hover:text-red-600 dark:hover:text-red-300 transition-colors duration-200"
              @click="handleLogout"
          >
            <LogOut class="h-3.5 w-3.5"/>
            Logout
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
  import { ref } from 'vue'
  import { onClickOutside } from '@vueuse/core'
  import { User, ChevronUp, Settings, LogOut, Sparkles } from 'lucide-vue-next'
  import { storeToRefs } from 'pinia'

  const props = defineProps<{
    isDark: boolean
    isLoggedIn?: boolean
  }>()

  const emit = defineEmits<{
    (e: 'close'): void
    (e: 'update:isDark', value: boolean): void
  }>()

  const isOpen = ref(false)
  const menuRef = ref<HTMLElement | null>(null)

  const { signOut } = useAuthClient()
  const userStore = useUserStore()
  const { profile, fullName } = storeToRefs(userStore)

  const isPro = computed(() => true)

  onClickOutside(menuRef, () => {
    if (isOpen.value) {
      handleClose()
    }
  })

  const handleClose = () => {
    isOpen.value = false
    emit('close')
  }

  const handleLogout = async () => {
    await signOut()
    userStore.clearProfile()
    handleClose()
    window.location.href = '/'
  }
</script>
