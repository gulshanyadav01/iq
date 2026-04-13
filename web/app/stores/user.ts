import {defineStore} from 'pinia'
import {computed, ref} from 'vue'
import {useAuthClient} from '../composables/useAuthClient'

export interface UserProfile {
  id: string
  firstName: string | null
  lastName: string | null
  email: string
  imageUrl?: string | null | undefined
}

export interface AuthUser {
  id: string
  name: string
  email: string
  image?: string | null | undefined
}

const STORAGE_KEY = 'user-profile'

export const useUserStore = defineStore('user', () => {
  const isLoggedIn = ref<boolean>(false)
  const profile = ref<UserProfile | null>(null)
  const loading = ref(false)
  const isLoaded = ref(false)
  const error = ref<Error | null>(null)
  const TOKEN_KEY = 'bearer-token'

  // Getters
  const fullName = computed(() => {
    if (!profile.value) return ''
    return [profile.value.firstName, profile.value.lastName].filter(Boolean).join(' ')
  })

  // Actions

  function setProfile(user: AuthUser) {
    if (!user) {
      profile.value = null
      isLoggedIn.value = false
      localStorage.removeItem(STORAGE_KEY)
      localStorage.removeItem(TOKEN_KEY)
      return
    }

    const nameSplit = user.name.split(' ')

    const userProfile = {
      id: user.id,
      firstName: nameSplit[0] || null,
      lastName: nameSplit.length > 1 ? nameSplit[nameSplit.length - 1] || null : null,
      email: user.email || '',
      imageUrl: user.image
    }

    profile.value = userProfile
    isLoggedIn.value = true
    isLoaded.value = true
    localStorage.setItem(STORAGE_KEY, JSON.stringify(userProfile))
  }

  function setToken(token: string) {
    localStorage.setItem(TOKEN_KEY, token);
  }

  async function getToken() {
    let token = localStorage.getItem(TOKEN_KEY);
    if (token && isTokenExpired(token)) {
      const {setJwtToken} = useAuthClient()
      token = await setJwtToken()
    }
    return token
  }

  function isTokenExpired(token: string) {
    try {
      // Split the token into parts
      const payloadBase64 = token.split('.')[1];
      if (!payloadBase64) {
        return true;
      }
      const decodedJson = atob(payloadBase64.replace(/-/g, '+').replace(/_/g, '/'));
      const decoded = JSON.parse(decodedJson);
      const exp = decoded.exp;

      // Compare expiration time with current time
      return Date.now() >= exp * 1000;
    } catch (error) {
      // If there's an error decoding, assume token is invalid/expired
      return true;
    }
  }

  function clearProfile() {
    profile.value = null
    isLoggedIn.value = false
    isLoaded.value = true
    localStorage.removeItem(STORAGE_KEY)
    localStorage.removeItem(TOKEN_KEY)
  }

  function loadStoredProfile() {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) {
      profile.value = JSON.parse(stored)
    }
    isLoaded.value = true
  }

  return {
    // State
    isLoggedIn,
    profile,
    loading,
    isLoaded,
    error,

    // Getters
    fullName,

    // Actions
    setProfile,
    setToken,
    getToken,
    clearProfile,
    loadStoredProfile
  }
})