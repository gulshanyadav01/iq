<template>
  <div v-if="enabledProviders.length > 0" class="space-y-4">
    <button
        v-for="provider in enabledProviders"
        :key="provider"
        type="button"
        @click="socialSignIn(provider)"
        class="w-full flex items-center justify-center gap-3 px-4 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
    >
      <img :src="providerConfig[provider].icon" :alt="providerConfig[provider].alt" class="h-5 w-5"/>
      {{ providerConfig[provider].label }}
    </button>
  </div>
</template>

<script setup lang="ts">
import type { SocialProvider } from '~/composables/useSocialProviders'

const { signIn } = useAuthClient()
const { enabledProviders, providerConfig } = useSocialProviders()

const socialSignIn = async (provider: SocialProvider) => {
  try {
    const config = useRuntimeConfig()

    await signIn.social({
      provider,
      callbackURL: `${config.public.appBaseUrl}/check-auth`
    })
  } catch (error) {
    console.error('Social sign in error:', error)
  }
}
</script>
