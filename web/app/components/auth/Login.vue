<template>
  <!-- Auth Forms -->
  <AuthForms @success="handleAuthSuccess" />
</template>

<script setup lang="ts">
import {storeToRefs} from "pinia";

const userStore = useUserStore()
const {isLoggedIn} = storeToRefs(userStore)

const handleAuthSuccess = () => {
  navigateTo('/home')
}

onMounted(() => {
  watch(() => isLoggedIn.value, (currentIsSignedIn) => {
    if (currentIsSignedIn) {
      navigateTo('/home')
    }
  }, {immediate: true})
})
</script>