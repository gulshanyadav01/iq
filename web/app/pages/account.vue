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

        <!-- Master Data Section -->
        <div class="p-6">
          <h2 class="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-2">Master Data</h2>
          <p class="text-xs text-gray-400 dark:text-gray-500 mb-4">
            Upload the Master Mapper Excel to refresh insurer, vehicle, fuel and plan mappings.
            Existing rows update; new rows are added.
          </p>

          <input
              ref="masterFileInput"
              type="file"
              class="hidden"
              accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              @change="handleMasterFileSelect"
          />

          <div class="flex items-center gap-3">
            <button
                type="button"
                class="px-3 py-1.5 rounded-lg text-xs font-medium text-gray-700 dark:text-gray-300 border border-gray-200/60 dark:border-white/[0.08] hover:bg-gray-50 dark:hover:bg-white/[0.04] transition-colors duration-200"
                @click="triggerMasterFileInput"
            >
              {{ masterFile ? 'Change file' : 'Choose file' }}
            </button>
            <span class="text-xs text-gray-500 dark:text-gray-400 truncate">
              {{ masterFile?.name || 'No file selected' }}
            </span>
            <button
                type="button"
                class="ml-auto px-3 py-1.5 rounded-lg bg-primary-600 text-white text-xs font-medium hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors duration-200"
                :disabled="!masterFile || masterUploading"
                @click="uploadMasterFile"
            >
              {{ masterUploading ? 'Uploading…' : 'Upload Master Sheet' }}
            </button>
          </div>

          <div v-if="masterStatus" class="mt-4">
            <div
                class="flex items-center gap-2 p-3 rounded-lg text-xs"
                :class="{
                  'bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400': masterStatus.type === 'error',
                  'bg-green-50 dark:bg-green-900/20 text-green-600 dark:text-green-400': masterStatus.type === 'success'
                }"
            >
              <component
                  :is="masterStatus.type === 'error' ? AlertCircle : CheckCircle2"
                  class="h-4 w-4 flex-shrink-0"
              />
              <p>{{ masterStatus.message }}</p>
            </div>
          </div>

          <div v-if="masterSummary" class="mt-4 overflow-hidden rounded-xl border border-gray-200/60 dark:border-white/[0.06]">
            <table class="w-full text-xs">
              <thead class="bg-gray-50/60 dark:bg-white/[0.03] text-gray-500 dark:text-gray-400">
                <tr>
                  <th class="text-left px-3 py-2 font-medium">Sheet</th>
                  <th class="text-right px-3 py-2 font-medium">Created</th>
                  <th class="text-right px-3 py-2 font-medium">Updated</th>
                  <th class="text-right px-3 py-2 font-medium">Skipped</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-gray-200/60 dark:divide-white/[0.04]">
                <tr v-for="(counts, sheet) in masterSummary" :key="sheet">
                  <td class="px-3 py-2 text-gray-700 dark:text-gray-300">{{ sheet }}</td>
                  <td class="px-3 py-2 text-right text-gray-700 dark:text-gray-300">{{ counts.created }}</td>
                  <td class="px-3 py-2 text-right text-gray-700 dark:text-gray-300">{{ counts.updated }}</td>
                  <td class="px-3 py-2 text-right text-gray-700 dark:text-gray-300">{{ counts.skipped }}</td>
                </tr>
              </tbody>
            </table>
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
const {getBaseUrl} = useBaseUrl()

const showPasswordDialog = ref(false)

// Master Data upload state
const masterFileInput = ref<HTMLInputElement | null>(null)
const masterFile = ref<File | null>(null)
const masterUploading = ref(false)
const masterStatus = ref<{ type: 'success' | 'error'; message: string } | null>(null)
type SheetCounts = { created: number; updated: number; skipped: number }
const masterSummary = ref<Record<string, SheetCounts> | null>(null)

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

const triggerMasterFileInput = () => {
  masterFileInput.value?.click()
}

const handleMasterFileSelect = (event: Event) => {
  const input = event.target as HTMLInputElement
  if (input.files?.length) {
    masterFile.value = input.files[0]
    masterStatus.value = null
    masterSummary.value = null
  }
}

const uploadMasterFile = async () => {
  if (!masterFile.value) return
  masterUploading.value = true
  masterStatus.value = null
  masterSummary.value = null

  try {
    const formData = new FormData()
    formData.append('file', masterFile.value)
    const token = await userStore.getToken()
    const response = await $fetch<{ status: string; summary: Record<string, SheetCounts>; error_message?: string }>(
        `${getBaseUrl()}/api/master-data/uploads/`,
        {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}` },
          body: formData,
        }
    )
    if (response.status === 'success') {
      masterSummary.value = response.summary
      masterStatus.value = { type: 'success', message: 'Master sheet uploaded successfully.' }
      masterFile.value = null
      if (masterFileInput.value) masterFileInput.value.value = ''
    } else {
      masterStatus.value = { type: 'error', message: response.error_message || 'Upload failed.' }
    }
  } catch (error: unknown) {
    const err = error as { data?: { error_message?: string; detail?: string }; message?: string }
    const detail = err.data?.error_message || err.data?.detail || err.message || 'Upload failed.'
    masterStatus.value = { type: 'error', message: detail }
  } finally {
    masterUploading.value = false
  }
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
