export default defineNuxtRouteMiddleware((to, from) => {
  const userStore = useUserStore()
  
  // If user is not logged in, redirect to login page
  if (!userStore.isLoggedIn) {
    return navigateTo('/login')
  }
})
