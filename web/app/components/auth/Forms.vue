<template>
  <div class="w-full">
    <!-- Tabs -->
    <div class="flex border-b border-gray-200 dark:border-white/20 mb-6">
      <button
          class="flex-1 px-4 py-2.5 text-xs font-medium transition-colors duration-200 relative text-center"
          :class="activeTab === 'signin'
            ? 'text-gray-900 dark:text-white'
            : 'text-gray-400 dark:text-white/50 hover:text-gray-600 dark:hover:text-white/70'"
          @click="activeTab = 'signin'"
      >
        Sign In
        <div
            class="absolute bottom-0 left-0 right-0 h-0.5 bg-primary-500 dark:bg-white transform origin-left transition-transform duration-300"
            :class="activeTab === 'signin' ? 'scale-x-100' : 'scale-x-0'"
        />
      </button>
      <button
          class="flex-1 px-4 py-2.5 text-xs font-medium transition-colors duration-200 relative text-center"
          :class="activeTab === 'signup'
            ? 'text-gray-900 dark:text-white'
            : 'text-gray-400 dark:text-white/50 hover:text-gray-600 dark:hover:text-white/70'"
          @click="activeTab = 'signup'"
      >
        Sign Up
        <div
            class="absolute bottom-0 left-0 right-0 h-0.5 bg-primary-500 dark:bg-white transform origin-left transition-transform duration-300"
            :class="activeTab === 'signup' ? 'scale-x-100' : 'scale-x-0'"
        />
      </button>
    </div>

    <!-- Sign In Form -->
    <form v-if="activeTab === 'signin'" @submit.prevent="handleSignIn" class="space-y-4">
      <!-- Email -->
      <div>
        <label for="signin-email" class="block text-xs font-medium text-gray-600 dark:text-white/80 mb-1">
          Email Address
        </label>
        <input
            id="signin-email"
            autocomplete="email"
            v-model="signInForm.email"
            type="email"
            required
            class="w-full"
            :class="formErrors.email ? 'border-red-400 dark:border-red-400' : ''"
            placeholder="Enter your email"
        />
        <p v-if="formErrors.email" class="mt-1 text-[11px] text-red-500 dark:text-red-400">{{ formErrors.email }}</p>
      </div>

      <!-- Password -->
      <div>
        <div class="flex items-center justify-between mb-1">
          <label for="signin-password" class="block text-xs font-medium text-gray-600 dark:text-white/80">
            Password
          </label>
          <button
              type="button"
              class="text-[11px] text-primary-600 dark:text-white/60 hover:text-primary-700 dark:hover:text-white transition-colors underline underline-offset-2"
              @click="showForgotPassword = true"
          >
            Forgot password?
          </button>
        </div>
        <div class="relative">
          <input
              id="signin-password"
              autocomplete="current-password"
              v-model="signInForm.password"
              :type="showPassword ? 'text' : 'password'"
              required
              class="w-full pr-10"
              :class="formErrors.password ? 'border-red-400 dark:border-red-400' : ''"
              placeholder="Enter your password"
          />
          <button
              type="button"
              class="absolute inset-y-0 right-0 pr-3 flex items-center text-gray-400 dark:text-white/50 hover:text-gray-600 dark:hover:text-white transition-colors"
              @click="showPassword = !showPassword"
          >
            <Eye v-if="showPassword" class="h-4 w-4"/>
            <EyeOff v-else class="h-4 w-4"/>
          </button>
        </div>
        <p v-if="formErrors.password" class="mt-1 text-[11px] text-red-500 dark:text-red-400">{{ formErrors.password }}</p>
      </div>

      <!-- Remember Me -->
      <div class="flex items-center gap-2">
        <input
            id="remember-me"
            v-model="signInForm.rememberMe"
            type="checkbox"
            class="h-3.5 w-3.5 rounded border-gray-300 dark:border-white/20 text-primary-600 focus:ring-primary-500/50"
        />
        <label for="remember-me" class="text-xs text-gray-500 dark:text-white/70">
          Remember me for 30 days
        </label>
      </div>

      <!-- Error Message -->
      <div v-if="authError" class="p-3 rounded-xl bg-red-50 dark:bg-red-500/10 border border-red-200 dark:border-red-400/20 text-red-600 dark:text-red-300 text-xs">
        {{ authError }}
      </div>

      <!-- Signup Success Message -->
      <div v-if="signUpSuccess" class="p-3 rounded-xl bg-green-50 dark:bg-green-500/10 border border-green-200 dark:border-green-400/20 text-green-700 dark:text-green-300 text-xs">
        <div class="flex items-start gap-2">
          <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 mt-0.5 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
            <polyline points="22 4 12 14.01 9 11.01"></polyline>
          </svg>
          <div>
            <p class="font-medium">Account created successfully!</p>
            <p class="text-[11px] mt-0.5 opacity-80">Please check your email to verify your account before signing in.</p>
          </div>
        </div>
      </div>

      <!-- Submit Button -->
      <button
          type="submit"
          class="w-full px-4 py-2 rounded-xl bg-primary-600 text-white text-xs font-medium hover:bg-primary-700 dark:bg-white/20 dark:border dark:border-white/30 dark:hover:bg-white/30 transition-colors duration-200 flex items-center justify-center shadow-sm"
          :disabled="isLoading"
      >
        <Loader2 v-if="isLoading" class="h-4 w-4 mr-2 animate-spin"/>
        {{ isLoading ? 'Signing In…' : 'Sign In' }}
      </button>

      <!-- Social Login Divider -->
      <template v-if="hasSocialProviders">
        <div class="relative my-4">
          <div class="absolute inset-0 flex items-center">
            <div class="w-full border-t border-gray-200 dark:border-white/20"></div>
          </div>
          <div class="relative flex justify-center text-[11px]">
            <span class="px-3 bg-white/55 dark:bg-white/5 text-gray-400 dark:text-white/60 rounded-full backdrop-blur-sm">Or continue with</span>
          </div>
        </div>
        <div class="grid grid-cols-1 gap-2">
          <AuthSocial/>
        </div>
      </template>
    </form>

    <!-- Sign Up Form -->
    <form v-if="activeTab === 'signup'" @submit.prevent="handleSignUp" class="space-y-4">
      <!-- Full Name -->
      <div>
        <label for="signup-name" class="block text-xs font-medium text-gray-600 dark:text-white/80 mb-1">
          Full Name
        </label>
        <input
            id="signup-name"
            v-model="signUpForm.name"
            type="text"
            required
            class="w-full"
            :class="formErrors.name ? 'border-red-400 dark:border-red-400' : ''"
            placeholder="Enter your full name"
        />
        <p v-if="formErrors.name" class="mt-1 text-[11px] text-red-500 dark:text-red-400">{{ formErrors.name }}</p>
      </div>

      <!-- Email -->
      <div>
        <label for="signup-email" class="block text-xs font-medium text-gray-600 dark:text-white/80 mb-1">
          Email Address
        </label>
        <input
            id="signup-email"
            v-model="signUpForm.email"
            type="email"
            required
            class="w-full"
            :class="formErrors.email ? 'border-red-400 dark:border-red-400' : ''"
            placeholder="Enter your email address"
        />
        <p v-if="formErrors.email" class="mt-1 text-[11px] text-red-500 dark:text-red-400">{{ formErrors.email }}</p>
      </div>

      <!-- Password -->
      <div>
        <label for="signup-password" class="block text-xs font-medium text-gray-600 dark:text-white/80 mb-1">
          Password
        </label>
        <div class="relative">
          <input
              id="signup-password"
              v-model="signUpForm.password"
              :type="showPassword ? 'text' : 'password'"
              required
              class="w-full pr-10"
              :class="formErrors.password ? 'border-red-400 dark:border-red-400' : ''"
              placeholder="Create a strong password"
          />
          <button
              type="button"
              class="absolute inset-y-0 right-0 pr-3 flex items-center text-gray-400 dark:text-white/50 hover:text-gray-600 dark:hover:text-white transition-colors"
              @click="showPassword = !showPassword"
          >
            <Eye v-if="showPassword" class="h-4 w-4"/>
            <EyeOff v-else class="h-4 w-4"/>
          </button>
        </div>
        <p v-if="formErrors.password" class="mt-1 text-[11px] text-red-500 dark:text-red-400">{{ formErrors.password }}</p>
      </div>

      <!-- Password Strength Indicator -->
      <div v-if="signUpForm.password" class="space-y-2">
        <div class="flex items-center gap-3">
          <div class="h-1.5 flex-1 rounded-full bg-gray-200 dark:bg-white/20 overflow-hidden">
            <div
                class="h-full transition-all duration-500 rounded-full"
                :class="[
                  passwordStrength === 'weak'   ? 'w-1/3 bg-red-400' :
                  passwordStrength === 'medium' ? 'w-2/3 bg-yellow-400' :
                  'w-full bg-green-400'
                ]"
            ></div>
          </div>
          <span class="text-[11px] font-medium" :class="[
            passwordStrength === 'weak'   ? 'text-red-500 dark:text-red-400' :
            passwordStrength === 'medium' ? 'text-yellow-600 dark:text-yellow-400' :
            'text-green-600 dark:text-green-400'
          ]">
            {{ passwordStrength === 'weak' ? 'Weak' : passwordStrength === 'medium' ? 'Medium' : 'Strong' }}
          </span>
        </div>
        <ul class="text-[11px] space-y-1 text-gray-500 dark:text-white/60">
          <li class="flex items-center gap-1.5">
            <Check v-if="passwordCriteria.length" class="h-3 w-3 text-green-500 dark:text-green-400"/>
            <X v-else class="h-3 w-3 text-red-400"/>
            At least 8 characters
          </li>
          <li class="flex items-center gap-1.5">
            <Check v-if="passwordCriteria.uppercase" class="h-3 w-3 text-green-500 dark:text-green-400"/>
            <X v-else class="h-3 w-3 text-red-400"/>
            Contains uppercase letter
          </li>
          <li class="flex items-center gap-1.5">
            <Check v-if="passwordCriteria.number" class="h-3 w-3 text-green-500 dark:text-green-400"/>
            <X v-else class="h-3 w-3 text-red-400"/>
            Contains number
          </li>
        </ul>
      </div>

      <!-- Error Message -->
      <div v-if="authError" class="p-3 rounded-xl bg-red-50 dark:bg-red-500/10 border border-red-200 dark:border-red-400/20 text-red-600 dark:text-red-300 text-xs">
        {{ authError }}
      </div>

      <!-- Submit Button -->
      <button
          type="submit"
          class="w-full px-4 py-2 rounded-xl bg-primary-600 text-white text-xs font-medium hover:bg-primary-700 dark:bg-white/20 dark:border dark:border-white/30 dark:hover:bg-white/30 transition-colors duration-200 flex items-center justify-center shadow-sm"
          :disabled="isLoading"
      >
        <Loader2 v-if="isLoading" class="h-4 w-4 mr-2 animate-spin"/>
        {{ isLoading ? 'Creating Account…' : 'Create Account' }}
      </button>

      <!-- Social Login Divider -->
      <template v-if="hasSocialProviders">
        <div class="relative my-4">
          <div class="absolute inset-0 flex items-center">
            <div class="w-full border-t border-gray-200 dark:border-white/20"></div>
          </div>
          <div class="relative flex justify-center text-[11px]">
            <span class="px-3 bg-white/55 dark:bg-white/5 text-gray-400 dark:text-white/60 rounded-full backdrop-blur-sm">Or continue with</span>
          </div>
        </div>
        <div class="grid grid-cols-1 gap-2">
          <AuthSocial/>
        </div>
      </template>

      <!-- Terms Agreement -->
      <p class="text-[11px] text-center text-gray-400 dark:text-white/50 leading-relaxed">
        By signing up, you agree to our
        <NuxtLink to="/terms" class="text-gray-600 dark:text-white/70 hover:text-gray-900 dark:hover:text-white underline underline-offset-2">Terms of Service</NuxtLink>
        and
        <NuxtLink to="/privacy" class="text-gray-600 dark:text-white/70 hover:text-gray-900 dark:hover:text-white underline underline-offset-2">Privacy Policy</NuxtLink>
      </p>
    </form>
  </div>

  <!-- Forgot Password Dialog -->
  <TransitionRoot appear :show="showForgotPassword" as="template">
    <Dialog as="div" @close="showForgotPassword = false" class="relative z-50">
      <TransitionChild
          as="template"
          enter="duration-200 ease-out"
          enter-from="opacity-0"
          enter-to="opacity-100"
          leave="duration-150 ease-in"
          leave-from="opacity-100"
          leave-to="opacity-0"
      >
        <div class="fixed inset-0 bg-black/20 backdrop-blur-sm"/>
      </TransitionChild>

      <div class="fixed inset-0 overflow-y-auto">
        <div class="flex min-h-full items-center justify-center p-4">
          <TransitionChild
              as="template"
              enter="duration-200 ease-out"
              enter-from="opacity-0 scale-95"
              enter-to="opacity-100 scale-100"
              leave="duration-150 ease-in"
              leave-from="opacity-100 scale-100"
              leave-to="opacity-0 scale-95"
          >
            <DialogPanel class="w-full max-w-sm glass rounded-2xl shadow-2xl p-6">
              <div class="flex justify-between items-center mb-5">
                <DialogTitle as="h3" class="text-sm font-semibold text-gray-900 dark:text-white">
                  Reset Password
                </DialogTitle>
                <button
                    type="button"
                    class="text-gray-400 dark:text-white/50 hover:text-gray-600 dark:hover:text-white transition-colors"
                    @click="showForgotPassword = false"
                >
                  <X class="h-4 w-4"/>
                </button>
              </div>

              <form @submit.prevent="handleForgotPassword" class="space-y-4">
                <p class="text-xs text-gray-500 dark:text-white/70 leading-relaxed">
                  Enter your email address and we'll send you a link to reset your password.
                </p>

                <div>
                  <label for="reset-email" class="block text-xs font-medium text-gray-600 dark:text-white/80 mb-1">
                    Email Address
                  </label>
                  <input
                      id="reset-email"
                      v-model="forgotPasswordEmail"
                      type="email"
                      required
                      class="w-full"
                      placeholder="Enter your email address"
                  />
                </div>

                <!-- Success Message -->
                <div v-if="resetEmailSent" class="p-3 rounded-xl bg-green-50 dark:bg-green-500/10 border border-green-200 dark:border-green-400/20 text-green-700 dark:text-green-300 text-xs">
                  <div class="flex items-center gap-2">
                    <Check class="h-3.5 w-3.5"/>
                    <span>Password reset link sent! Check your email.</span>
                  </div>
                </div>

                <div class="flex justify-end gap-2">
                  <button
                      type="button"
                      class="px-3 py-1.5 rounded-lg text-xs font-medium text-gray-500 dark:text-white/60 hover:text-gray-700 dark:hover:text-white hover:bg-gray-100 dark:hover:bg-white/[0.06] transition-colors duration-200"
                      @click="showForgotPassword = false"
                  >
                    Cancel
                  </button>
                  <button
                      type="submit"
                      class="px-3 py-1.5 rounded-lg bg-primary-600 text-white text-xs font-medium hover:bg-primary-700 dark:bg-white/20 dark:border dark:border-white/30 dark:hover:bg-white/30 transition-colors duration-200 flex items-center"
                      :disabled="isLoading || resetEmailSent"
                  >
                    <Loader2 v-if="isLoading" class="h-3.5 w-3.5 mr-1.5 animate-spin"/>
                    {{ resetEmailSent ? 'Sent' : 'Send Reset Link' }}
                  </button>
                </div>
              </form>
            </DialogPanel>
          </TransitionChild>
        </div>
      </div>
    </Dialog>
  </TransitionRoot>
</template>

<script setup lang="ts">
import {computed, ref, watch} from 'vue'
import {Dialog, DialogPanel, DialogTitle, TransitionChild, TransitionRoot} from '@headlessui/vue'
import {Check, Eye, EyeOff, Loader2, X} from 'lucide-vue-next'

const props = defineProps<{
  initialTab?: 'signin' | 'signup'
}>()

const emit = defineEmits<{
  (e: 'success'): void
}>()

const isDark = ref(false)
const callbackURL = ref('')
const activeTab = ref(props.initialTab || 'signin')
const showPassword = ref(false)
const isLoading = ref(false)
const authError = ref('')
const signUpSuccess = ref<boolean>(false)
const showForgotPassword = ref(false)
const forgotPasswordEmail = ref('')
const resetEmailSent = ref(false)

const signInForm = ref({
  email: '',
  password: '',
  rememberMe: false
})

const signUpForm = ref({
  name: '',
  email: '',
  password: ''
})

const formErrors = ref({
  name: '',
  email: '',
  password: ''
})

const passwordCriteria = computed(() => {
  const password = signUpForm.value.password
  return {
    length: password.length >= 8,
    uppercase: /[A-Z]/.test(password),
    number: /[0-9]/.test(password)
  }
})

const passwordStrength = computed(() => {
  const criteria = passwordCriteria.value
  const score = Object.values(criteria).filter(Boolean).length
  if (score <= 1) return 'weak'
  if (score === 2) return 'medium'
  return 'strong'
})

watch(() => props.initialTab, (newTab) => {
  if (newTab) activeTab.value = newTab
})

onMounted(() => {
  const config = useRuntimeConfig()
  callbackURL.value = `${config.public.appBaseUrl}/check-auth`

  isDark.value = document.documentElement.classList.contains('dark')

  const observer = new MutationObserver(() => {
    isDark.value = document.documentElement.classList.contains('dark')
  })
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
  onUnmounted(() => observer.disconnect())
})

const {signIn, signUp} = useAuthClient()
const { hasSocialProviders } = useSocialProviders()

const handleSignIn = async () => {
  authError.value = ''
  signUpSuccess.value = false
  formErrors.value = {name: '', email: '', password: ''}

  if (!validateEmail(signInForm.value.email)) {
    formErrors.value.email = 'Please enter a valid email address'
    return
  }

  isLoading.value = true
  try {
    const {error} = await signIn.email({
      email: signInForm.value.email,
      password: signInForm.value.password,
      rememberMe: signInForm.value.rememberMe,
      callbackURL: callbackURL.value
    })
    if (error) throw error
    emit('success')
  } catch (error: any) {
    authError.value = error.message
  } finally {
    isLoading.value = false
  }
}

const handleSignUp = async () => {
  authError.value = ''
  signUpSuccess.value = false
  formErrors.value = {name: '', email: '', password: ''}

  if (signUpForm.value.name.length < 2) {
    formErrors.value.name = 'Name must be at least 2 characters'
    return
  }
  if (!validateEmail(signUpForm.value.email)) {
    formErrors.value.email = 'Please enter a valid email address'
    return
  }
  if (signUpForm.value.password.length < 8) {
    formErrors.value.password = 'Password must be at least 8 characters'
    return
  }

  isLoading.value = true
  try {
    const {error} = await signUp.email({
      email: signUpForm.value.email,
      password: signUpForm.value.password,
      name: signUpForm.value.name,
      callbackURL: callbackURL.value
    })
    if (error) throw error

    authError.value = ''
    activeTab.value = 'signin'
    signUpSuccess.value = true
    signInForm.value.email = signUpForm.value.email
    signUpForm.value = {name: '', email: '', password: ''}
  } catch (error: any) {
    authError.value = error.message
  } finally {
    isLoading.value = false
  }
}

const handleForgotPassword = async () => {
  if (!validateEmail(forgotPasswordEmail.value)) return

  isLoading.value = true
  try {
    await new Promise(resolve => setTimeout(resolve, 1000))
    resetEmailSent.value = true
    setTimeout(() => {
      showForgotPassword.value = false
      resetEmailSent.value = false
      forgotPasswordEmail.value = ''
    }, 10000)
  } catch (error) {
    console.error('Forgot password error:', error)
  } finally {
    isLoading.value = false
  }
}

const validateEmail = (email: string) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)
</script>
