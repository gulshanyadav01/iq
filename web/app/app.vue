<template>
  <NuxtLoadingIndicator/>
  <NuxtLayout>
    <NuxtPage/>
  </NuxtLayout>
</template>

<script setup lang="ts">
import {useUserStore} from "./stores/user";
import {useAuthClient} from "./composables/useAuthClient";

const isLoading = ref<boolean>(true)

const forceLogOut = async (signOutFromAuth: boolean) => {
  if (signOutFromAuth) {
    const {signOut} = useAuthClient()
    await signOut()
  }

  const userStore = useUserStore()
  userStore.clearProfile()
}

const updateSession = async () => {
  try {
    const {getSession, setJwtToken} = useAuthClient()

    await setJwtToken()

    // Get session data
    const session = await getSession({fetchOptions: {credentials: 'include'}})

    if (session.data) {
      const userStore = useUserStore()
      userStore.setProfile({
        ...session.data.user,
      })
    }
  } catch (e: any) {
    await forceLogOut(e.status !== 401)
  }
}

onMounted(async () => {
  await updateSession()
  isLoading.value = false
});

useHead({
  title: 'InsurIQ',
  meta: [
    {
      name: 'description',
      content: 'InsurIQ'
    }
  ]
})

useSeoMeta({
  title: 'InsurIQ',
  description: 'InsurIQ',
  ogTitle: 'InsurIQ',
  ogDescription: 'InsurIQ',
  ogImage: 'https://rhobots.ai/images/og-image.png',
  ogUrl: 'https://rhobots.ai',
  ogType: 'website',
  twitterCard: 'summary_large_image',
  twitterTitle: 'InsurIQ',
  twitterDescription: 'InsurIQ',
  twitterImage: 'https://rhobots.ai/images/og-image.png',
  robots: 'index, follow',
});
</script>