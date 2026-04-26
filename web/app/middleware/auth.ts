export default defineNuxtRouteMiddleware(() => {
  if (import.meta.client) {
    const userStore = useUserStore()
    if (!userStore.isLoaded) {
      userStore.loadStoredProfile()
    }
    if (!userStore.isLoggedIn) {
      return navigateTo('/login')
    }
  }
})
