<template>
  <div class="p-8 md:p-10">
    <div v-if="profile" class="max-w-xl mx-auto">
      <div class="mb-10">
        <h1 class="text-xl font-semibold tracking-tight text-gray-900 dark:text-gray-100">Account Settings</h1>
        <p class="mt-1 text-xs text-gray-400 dark:text-gray-500">Manage your account preferences and personal information</p>
      </div>

      <!-- Account Label -->
      <div class="mb-6">
        <div class="flex items-center gap-2">
          <div class="px-2 py-0.5 rounded-full text-[10px] font-medium glass-subtle text-gray-500 dark:text-gray-400 border-0">
            Free Account
          </div>
        </div>
      </div>

      <!-- Profile Section -->
      <div class="glass rounded-2xl divide-y divide-gray-200/60 dark:divide-white/[0.06]">
        <div class="p-6">
          <h2 class="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-6">Profile Information</h2>

          <div class="flex items-start gap-6">
            <!-- Profile Picture -->
            <div class="flex flex-col items-center gap-2">
              <div class="relative group">
                <div class="h-16 w-16 rounded-full bg-primary-100 dark:bg-primary-900/40 flex items-center justify-center ring-2 ring-white dark:ring-white/10">
                  <img
                      v-if="profile?.imageUrl"
                      :src="profile.imageUrl"
                      :alt="fullName"
                      class="h-full w-full object-cover rounded-full"
                  />
                  <User v-else class="h-6 w-6 text-primary-600 dark:text-primary-400"/>
                </div>
                <button
                    class="absolute inset-0 flex items-center justify-center rounded-full bg-black/50 opacity-0 group-hover:opacity-100 transition-opacity duration-200"
                    @click="triggerFileInput"
                >
                  <Camera class="h-4 w-4 text-white"/>
                </button>
                <input
                    ref="fileInput"
                    type="file"
                    class="hidden"
                    accept="image/*"
                    @change="handleProfilePicture"
                />
              </div>
              <button
                  class="text-[11px] text-gray-400 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition-colors duration-200"
                  @click="triggerFileInput"
              >
                Change photo
              </button>
            </div>

            <!-- Profile Form -->
            <form class="flex-1 space-y-4" @submit.prevent="saveProfile">
              <div>
                <label for="name" class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
                  Full Name
                </label>
                <input
                    id="name"
                    v-model="fullName"
                    type="text"
                    class="w-full"
                />
              </div>

              <div>
                <label for="email" class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
                  Email Address
                </label>
                <input
                    id="email"
                    v-model="profile.email"
                    type="email"
                    class="w-full"
                />
              </div>

              <div class="pt-2">
                <button
                    type="submit"
                    class="px-3 py-1.5 rounded-lg bg-primary-600 text-white text-xs font-medium hover:bg-primary-700 transition-colors duration-200"
                >
                  Save Changes
                </button>
              </div>
            </form>
          </div>
        </div>

        <!-- Password Section -->
        <div class="p-6">
          <div class="flex items-center justify-between">
            <h2 class="text-sm font-semibold text-gray-900 dark:text-gray-100">Password</h2>
            <button
                class="px-3 py-1.5 rounded-lg text-xs font-medium text-primary-600 dark:text-primary-400 hover:bg-primary-50 dark:hover:bg-primary-900/20 transition-colors duration-200"
                @click="showPasswordDialog = true"
            >
              Change Password
            </button>
          </div>
        </div>
      </div>

      <!-- Password Change Dialog -->
      <TransitionRoot appear :show="showPasswordDialog" as="template">
        <Dialog as="div" @close="showPasswordDialog = false" class="relative z-10">
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
                <DialogPanel class="w-full max-w-md glass rounded-2xl shadow-2xl p-6">
                  <DialogTitle as="h3" class="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-5">
                    Change Password
                  </DialogTitle>

                  <form @submit.prevent="changePassword" class="space-y-4">
                    <div>
                      <label for="current-password" class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
                        Current Password
                      </label>
                      <input id="current-password" v-model="passwordForm.current" type="password" class="w-full" />
                    </div>

                    <div>
                      <label for="new-password" class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
                        New Password
                      </label>
                      <input id="new-password" v-model="passwordForm.new" type="password" class="w-full" />
                    </div>

                    <div>
                      <label for="confirm-password" class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
                        Confirm New Password
                      </label>
                      <input id="confirm-password" v-model="passwordForm.confirm" type="password" class="w-full" />
                    </div>

                    <!-- Status Message -->
                    <div v-if="passwordChangeStatus" class="mt-3">
                      <div
                          class="flex items-center gap-2 p-3 rounded-lg text-xs"
                          :class="{
                          'bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400': passwordChangeStatus.type === 'error',
                          'bg-green-50 dark:bg-green-900/20 text-green-600 dark:text-green-400': passwordChangeStatus.type === 'success'
                        }"
                      >
                        <component
                            :is="passwordChangeStatus.type === 'error' ? AlertCircle : CheckCircle2"
                            class="h-4 w-4 flex-shrink-0"
                        />
                        <p>{{ passwordChangeStatus.message }}</p>
                      </div>
                    </div>

                    <div class="flex justify-end gap-2 mt-6">
                      <button
                          type="button"
                          class="px-3 py-1.5 rounded-lg text-xs font-medium text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-white/[0.06] transition-colors duration-200"
                          @click="showPasswordDialog = false"
                      >
                        Cancel
                      </button>
                      <button
                          type="submit"
                          class="px-3 py-1.5 rounded-lg bg-primary-600 text-white text-xs font-medium hover:bg-primary-700 transition-colors duration-200"
                      >
                        Update Password
                      </button>
                    </div>
                  </form>
                </DialogPanel>
              </TransitionChild>
            </div>
          </div>
        </Dialog>
      </TransitionRoot>
    </div>
  </div>
</template>

<script setup lang="ts">
import {AlertCircle, Camera, CheckCircle2, User} from 'lucide-vue-next'
import {Dialog, DialogPanel, DialogTitle, TransitionChild, TransitionRoot} from '@headlessui/vue'

const fileInput = ref<HTMLInputElement | null>(null)
const userStore = useUserStore()
const {profile, fullName} = storeToRefs(userStore)

const showPasswordDialog = ref(false)

const passwordForm = ref({
  current: '',
  new: '',
  confirm: ''
})

const passwordChangeStatus = ref<{
  type: 'success' | 'error'
  message: string
} | null>(null)

const triggerFileInput = () => {
  fileInput.value?.click()
}

const handleProfilePicture = (event: Event) => {
  const input = event.target as HTMLInputElement
  if (input.files?.length) {
    console.log('Uploading profile picture:', input.files[0].name)
  }
}

const saveProfile = () => {
  // TODO: Implement profile update
}

const changePassword = async () => {
  if (!passwordForm.value.new || !passwordForm.value.confirm || passwordForm.value.new !== passwordForm.value.confirm) {
    passwordChangeStatus.value = {
      type: 'error',
      message: 'New passwords do not match'
    }
    return
  }

  try {
    passwordChangeStatus.value = {
      type: 'success',
      message: 'Password updated successfully'
    }
    passwordForm.value = { current: '', new: '', confirm: '' }
    showPasswordDialog.value = false
  } catch (error: any) {
    const errorData = error.errors?.[0] || {}
    passwordChangeStatus.value = {
      type: 'error',
      message: errorData.long_message || errorData.message || 'Failed to update password'
    }
  }
}
</script>
