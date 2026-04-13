<template>
  <div class="p-6">
    <!-- Standard width container -->
    <div class="max-w-4xl mx-auto">
      
      <!-- Page Header -->
      <div class="mb-8">
        <h1 class="text-3xl font-bold text-gray-900 dark:text-gray-100">
          UI Components Sample
        </h1>
        <p class="mt-2 text-lg text-gray-600 dark:text-gray-400">
          A comprehensive reference of UI components and patterns used in this application
        </p>
      </div>

      <!-- Navigation Tabs -->
      <div class="mb-8">
        <nav class="flex space-x-8" aria-label="Tabs">
          <button
            v-for="tab in tabs"
            :key="tab.id"
            @click="activeTab = tab.id"
            :class="[
              activeTab === tab.id
                ? 'border-primary-500 text-primary-600 dark:text-primary-400'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300 dark:text-gray-400 dark:hover:text-gray-300',
              'whitespace-nowrap py-2 px-1 border-b-2 font-medium text-sm'
            ]"
          >
            {{ tab.name }}
          </button>
        </nav>
      </div>

      <!-- Content based on active tab -->
      <div class="space-y-8">
        
        <!-- Typography Section -->
        <section v-show="activeTab === 'typography'" class="space-y-6">
          <div class="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
            <h2 class="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">Typography</h2>
            <div class="space-y-4">
              <div>
                <h1 class="text-4xl font-bold text-gray-900 dark:text-gray-100">Heading 1 (4xl)</h1>
                <code class="text-sm text-gray-500">text-4xl font-bold</code>
              </div>
              <div>
                <h2 class="text-3xl font-semibold text-gray-900 dark:text-gray-100">Heading 2 (3xl)</h2>
                <code class="text-sm text-gray-500">text-3xl font-semibold</code>
              </div>
              <div>
                <h3 class="text-2xl font-medium text-gray-900 dark:text-gray-100">Heading 3 (2xl)</h3>
                <code class="text-sm text-gray-500">text-2xl font-medium</code>
              </div>
              <div>
                <p class="text-base text-gray-700 dark:text-gray-300">Body text (base) - Lorem ipsum dolor sit amet, consectetur adipiscing elit.</p>
                <code class="text-sm text-gray-500">text-base text-gray-700</code>
              </div>
              <div>
                <p class="text-sm text-gray-500 dark:text-gray-400">Small text - Secondary information</p>
                <code class="text-sm text-gray-500">text-sm text-gray-500</code>
              </div>
            </div>
          </div>
        </section>

        <!-- Buttons Section -->
        <section v-show="activeTab === 'buttons'" class="space-y-6">
          <div class="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
            <h2 class="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">Buttons</h2>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
              
              <!-- Primary Buttons -->
              <div class="space-y-3">
                <h3 class="font-medium text-gray-900 dark:text-gray-100">Primary Buttons</h3>
                <div class="flex flex-wrap gap-3">
                  <button class="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors">
                    Primary
                  </button>
                  <button class="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors flex items-center gap-2">
                    <Plus class="h-4 w-4" />
                    With Icon
                  </button>
                  <button class="px-4 py-2 bg-primary-600 text-white rounded-lg opacity-50 cursor-not-allowed">
                    Disabled
                  </button>
                </div>
                <code class="text-sm text-gray-500 block">px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700</code>
              </div>

              <!-- Secondary Buttons -->
              <div class="space-y-3">
                <h3 class="font-medium text-gray-900 dark:text-gray-100">Secondary Buttons</h3>
                <div class="flex flex-wrap gap-3">
                  <button class="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
                    Secondary
                  </button>
                  <button class="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors flex items-center gap-2">
                    <Download class="h-4 w-4" />
                    Download
                  </button>
                </div>
                <code class="text-sm text-gray-500 block">px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50</code>
              </div>

              <!-- Ghost Buttons -->
              <div class="space-y-3">
                <h3 class="font-medium text-gray-900 dark:text-gray-100">Ghost Buttons</h3>
                <div class="flex flex-wrap gap-3">
                  <button class="px-4 py-2 text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors">
                    Ghost
                  </button>
                  <button class="px-4 py-2 text-red-600 dark:text-red-400 rounded-lg hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors flex items-center gap-2">
                    <Trash2 class="h-4 w-4" />
                    Delete
                  </button>
                </div>
                <code class="text-sm text-gray-500 block">px-4 py-2 text-gray-700 rounded-lg hover:bg-gray-100</code>
              </div>

              <!-- Icon Buttons -->
              <div class="space-y-3">
                <h3 class="font-medium text-gray-900 dark:text-gray-100">Icon Buttons</h3>
                <div class="flex flex-wrap gap-3">
                  <button class="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors">
                    <Settings class="h-5 w-5 text-gray-600 dark:text-gray-400" />
                  </button>
                  <button class="p-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors">
                    <Plus class="h-5 w-5" />
                  </button>
                  <button class="p-2 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
                    <Search class="h-5 w-5 text-gray-600 dark:text-gray-400" />
                  </button>
                </div>
                <code class="text-sm text-gray-500 block">p-2 rounded-lg hover:bg-gray-100</code>
              </div>
            </div>
          </div>
        </section>

        <!-- Forms Section -->
        <section v-show="activeTab === 'forms'" class="space-y-6">
          <div class="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
            <h2 class="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">Form Elements</h2>
            <form @submit.prevent class="space-y-6">
              
              <!-- Text Inputs -->
              <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    Text Input
                  </label>
                  <input 
                    type="text" 
                    placeholder="Enter text..."
                    class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                  />
                </div>
                <div>
                  <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    Email Input
                  </label>
                  <input 
                    type="email" 
                    placeholder="Enter email..."
                    class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                  />
                </div>
              </div>

              <!-- Select Dropdown -->
              <div>
                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Select Dropdown
                </label>
                <select class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-primary-500 focus:border-transparent">
                  <option>Choose an option...</option>
                  <option>Option 1</option>
                  <option>Option 2</option>
                  <option>Option 3</option>
                </select>
              </div>

              <!-- Textarea -->
              <div>
                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Textarea
                </label>
                <textarea 
                  rows="4"
                  placeholder="Enter your message..."
                  class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                ></textarea>
              </div>

              <!-- Checkboxes and Radio -->
              <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div>
                  <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">
                    Checkboxes
                  </label>
                  <div class="space-y-2">
                    <label class="flex items-center">
                      <input type="checkbox" class="rounded border-gray-300 text-primary-600 focus:ring-primary-500">
                      <span class="ml-2 text-sm text-gray-700 dark:text-gray-300">Option 1</span>
                    </label>
                    <label class="flex items-center">
                      <input type="checkbox" class="rounded border-gray-300 text-primary-600 focus:ring-primary-500" checked>
                      <span class="ml-2 text-sm text-gray-700 dark:text-gray-300">Option 2 (checked)</span>
                    </label>
                  </div>
                </div>
                <div>
                  <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">
                    Radio Buttons
                  </label>
                  <div class="space-y-2">
                    <label class="flex items-center">
                      <input type="radio" name="radio-example" class="border-gray-300 text-primary-600 focus:ring-primary-500">
                      <span class="ml-2 text-sm text-gray-700 dark:text-gray-300">Choice 1</span>
                    </label>
                    <label class="flex items-center">
                      <input type="radio" name="radio-example" class="border-gray-300 text-primary-600 focus:ring-primary-500" checked>
                      <span class="ml-2 text-sm text-gray-700 dark:text-gray-300">Choice 2 (selected)</span>
                    </label>
                  </div>
                </div>
              </div>

              <!-- Form Actions -->
              <div class="flex justify-end gap-3 pt-4">
                <button type="button" class="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
                  Cancel
                </button>
                <button type="submit" class="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors">
                  Submit
                </button>
              </div>
            </form>
          </div>
        </section>

        <!-- Cards Section -->
        <section v-show="activeTab === 'cards'" class="space-y-6">
          <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            
            <!-- Basic Card -->
            <div class="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
              <h3 class="text-lg font-medium text-gray-900 dark:text-gray-100 mb-2">Basic Card</h3>
              <p class="text-gray-600 dark:text-gray-400 text-sm">
                A simple card with basic content and padding.
              </p>
              <div class="mt-4">
                <button class="text-primary-600 dark:text-primary-400 text-sm font-medium hover:underline">
                  Learn more
                </button>
              </div>
            </div>

            <!-- Card with Icon -->
            <div class="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
              <div class="flex items-center gap-3 mb-3">
                <div class="p-2 bg-primary-100 dark:bg-primary-900/50 rounded-lg">
                  <Users class="h-5 w-5 text-primary-600 dark:text-primary-400" />
                </div>
                <h3 class="text-lg font-medium text-gray-900 dark:text-gray-100">With Icon</h3>
              </div>
              <p class="text-gray-600 dark:text-gray-400 text-sm">
                Card with an icon header for better visual hierarchy.
              </p>
            </div>

            <!-- Stat Card -->
            <div class="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
              <div class="flex items-center justify-between">
                <div>
                  <p class="text-sm font-medium text-gray-600 dark:text-gray-400">Total Users</p>
                  <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">1,234</p>
                </div>
                <div class="p-3 bg-green-100 dark:bg-green-900/50 rounded-full">
                  <TrendingUp class="h-6 w-6 text-green-600 dark:text-green-400" />
                </div>
              </div>
              <div class="mt-4 flex items-center">
                <span class="text-sm text-green-600 dark:text-green-400 font-medium">+12%</span>
                <span class="text-sm text-gray-500 dark:text-gray-400 ml-1">from last month</span>
              </div>
            </div>

            <!-- Action Card -->
            <div class="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
              <div class="p-6">
                <h3 class="text-lg font-medium text-gray-900 dark:text-gray-100 mb-2">Action Card</h3>
                <p class="text-gray-600 dark:text-gray-400 text-sm mb-4">
                  Card with action buttons in the footer.
                </p>
              </div>
              <div class="px-6 py-3 bg-gray-50 dark:bg-gray-800 border-t border-gray-200 dark:border-gray-700 flex justify-between items-center">
                <span class="text-sm text-gray-500 dark:text-gray-400">Last updated: 2 hours ago</span>
                <button class="text-primary-600 dark:text-primary-400 text-sm font-medium hover:underline">
                  View details
                </button>
              </div>
            </div>

            <!-- Alert Card -->
            <div class="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg p-6">
              <div class="flex items-start gap-3">
                <AlertTriangle class="h-5 w-5 text-amber-600 dark:text-amber-400 mt-0.5" />
                <div>
                  <h3 class="text-sm font-medium text-amber-800 dark:text-amber-200">Warning</h3>
                  <p class="text-sm text-amber-700 dark:text-amber-300 mt-1">
                    This is a warning message card.
                  </p>
                </div>
              </div>
            </div>

            <!-- Success Card -->
            <div class="bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg p-6">
              <div class="flex items-start gap-3">
                <CheckCircle2 class="h-5 w-5 text-green-600 dark:text-green-400 mt-0.5" />
                <div>
                  <h3 class="text-sm font-medium text-green-800 dark:text-green-200">Success</h3>
                  <p class="text-sm text-green-700 dark:text-green-300 mt-1">
                    Operation completed successfully.
                  </p>
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- Alerts Section -->
        <section v-show="activeTab === 'alerts'" class="space-y-6">
          <div class="space-y-4">
            
            <!-- Info Alert -->
            <div class="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-4">
              <div class="flex items-start gap-3">
                <Info class="h-5 w-5 text-blue-600 dark:text-blue-400 mt-0.5" />
                <div class="flex-1">
                  <h3 class="text-sm font-medium text-blue-800 dark:text-blue-200">Information</h3>
                  <p class="text-sm text-blue-700 dark:text-blue-300 mt-1">
                    This is an informational alert with additional context and details.
                  </p>
                </div>
                <button class="text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-200">
                  <X class="h-4 w-4" />
                </button>
              </div>
            </div>

            <!-- Success Alert -->
            <div class="bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg p-4">
              <div class="flex items-start gap-3">
                <CheckCircle2 class="h-5 w-5 text-green-600 dark:text-green-400 mt-0.5" />
                <div class="flex-1">
                  <h3 class="text-sm font-medium text-green-800 dark:text-green-200">Success</h3>
                  <p class="text-sm text-green-700 dark:text-green-300 mt-1">
                    Your changes have been saved successfully.
                  </p>
                </div>
              </div>
            </div>

            <!-- Warning Alert -->
            <div class="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg p-4">
              <div class="flex items-start gap-3">
                <AlertTriangle class="h-5 w-5 text-amber-600 dark:text-amber-400 mt-0.5" />
                <div class="flex-1">
                  <h3 class="text-sm font-medium text-amber-800 dark:text-amber-200">Warning</h3>
                  <p class="text-sm text-amber-700 dark:text-amber-300 mt-1">
                    Please review your settings before proceeding.
                  </p>
                </div>
              </div>
            </div>

            <!-- Error Alert -->
            <div class="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-4">
              <div class="flex items-start gap-3">
                <AlertCircle class="h-5 w-5 text-red-600 dark:text-red-400 mt-0.5" />
                <div class="flex-1">
                  <h3 class="text-sm font-medium text-red-800 dark:text-red-200">Error</h3>
                  <p class="text-sm text-red-700 dark:text-red-300 mt-1">
                    There was an error processing your request. Please try again.
                  </p>
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- Layout Section -->
        <section v-show="activeTab === 'layout'" class="space-y-6">
          
          <!-- Grid Examples -->
          <div class="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
            <h2 class="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">Grid Layouts</h2>
            
            <div class="space-y-6">
              <div>
                <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">2 Column Grid</h3>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div class="bg-gray-100 dark:bg-gray-800 rounded-lg p-4 text-center">
                    <span class="text-sm text-gray-600 dark:text-gray-400">Column 1</span>
                  </div>
                  <div class="bg-gray-100 dark:bg-gray-800 rounded-lg p-4 text-center">
                    <span class="text-sm text-gray-600 dark:text-gray-400">Column 2</span>
                  </div>
                </div>
                <code class="text-xs text-gray-500 mt-2 block">grid grid-cols-1 md:grid-cols-2 gap-4</code>
              </div>

              <div>
                <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">3 Column Grid</h3>
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  <div class="bg-gray-100 dark:bg-gray-800 rounded-lg p-4 text-center">
                    <span class="text-sm text-gray-600 dark:text-gray-400">Column 1</span>
                  </div>
                  <div class="bg-gray-100 dark:bg-gray-800 rounded-lg p-4 text-center">
                    <span class="text-sm text-gray-600 dark:text-gray-400">Column 2</span>
                  </div>
                  <div class="bg-gray-100 dark:bg-gray-800 rounded-lg p-4 text-center">
                    <span class="text-sm text-gray-600 dark:text-gray-400">Column 3</span>
                  </div>
                </div>
                <code class="text-xs text-gray-500 mt-2 block">grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4</code>
              </div>
            </div>
          </div>

          <!-- Flex Examples -->
          <div class="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
            <h2 class="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">Flexbox Layouts</h2>
            
            <div class="space-y-6">
              <div>
                <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">Space Between</h3>
                <div class="flex justify-between items-center bg-gray-50 dark:bg-gray-800 rounded-lg p-4">
                  <span class="text-sm text-gray-600 dark:text-gray-400">Left</span>
                  <span class="text-sm text-gray-600 dark:text-gray-400">Right</span>
                </div>
                <code class="text-xs text-gray-500 mt-2 block">flex justify-between items-center</code>
              </div>

              <div>
                <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">Center Aligned</h3>
                <div class="flex justify-center items-center bg-gray-50 dark:bg-gray-800 rounded-lg p-4">
                  <span class="text-sm text-gray-600 dark:text-gray-400">Centered Content</span>
                </div>
                <code class="text-xs text-gray-500 mt-2 block">flex justify-center items-center</code>
              </div>

              <div>
                <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">Flex Wrap</h3>
                <div class="flex flex-wrap gap-2 bg-gray-50 dark:bg-gray-800 rounded-lg p-4">
                  <span class="px-3 py-1 bg-primary-100 dark:bg-primary-900/50 text-primary-700 dark:text-primary-300 rounded-full text-sm">Tag 1</span>
                  <span class="px-3 py-1 bg-primary-100 dark:bg-primary-900/50 text-primary-700 dark:text-primary-300 rounded-full text-sm">Tag 2</span>
                  <span class="px-3 py-1 bg-primary-100 dark:bg-primary-900/50 text-primary-700 dark:text-primary-300 rounded-full text-sm">Tag 3</span>
                  <span class="px-3 py-1 bg-primary-100 dark:bg-primary-900/50 text-primary-700 dark:text-primary-300 rounded-full text-sm">Tag 4</span>
                </div>
                <code class="text-xs text-gray-500 mt-2 block">flex flex-wrap gap-2</code>
              </div>
            </div>
          </div>
        </section>

        <!-- Interactive Elements -->
        <section v-show="activeTab === 'interactive'" class="space-y-6">
          
          <!-- Modal Demo -->
          <div class="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
            <h2 class="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">Interactive Elements</h2>
            
            <div class="space-y-4">
              <div>
                <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">Modal Dialog</h3>
                <button 
                  @click="showModal = true"
                  class="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors"
                >
                  Open Modal
                </button>
              </div>

              <div>
                <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">Toggle Switch</h3>
                <label class="flex items-center cursor-pointer">
                  <input 
                    type="checkbox" 
                    v-model="toggleState"
                    class="sr-only"
                  />
                  <div class="relative">
                    <div 
                      :class="[
                        'block w-14 h-8 rounded-full transition-colors',
                        toggleState ? 'bg-primary-600' : 'bg-gray-300 dark:bg-gray-600'
                      ]"
                    ></div>
                    <div 
                      :class="[
                        'absolute left-1 top-1 bg-white w-6 h-6 rounded-full transition-transform',
                        toggleState ? 'transform translate-x-6' : ''
                      ]"
                    ></div>
                  </div>
                  <span class="ml-3 text-sm text-gray-700 dark:text-gray-300">
                    {{ toggleState ? 'Enabled' : 'Disabled' }}
                  </span>
                </label>
              </div>

              <div>
                <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">Progress Bar</h3>
                <div class="space-y-2">
                  <div class="flex justify-between text-sm">
                    <span class="text-gray-600 dark:text-gray-400">Progress</span>
                    <span class="text-gray-600 dark:text-gray-400">{{ progress }}%</span>
                  </div>
                  <div class="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2">
                    <div 
                      class="bg-primary-600 h-2 rounded-full transition-all duration-300"
                      :style="{ width: progress + '%' }"
                    ></div>
                  </div>
                  <button 
                    @click="updateProgress"
                    class="text-sm text-primary-600 dark:text-primary-400 hover:underline"
                  >
                    Update Progress
                  </button>
                </div>
              </div>

              <div>
                <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">Dropdown Menu</h3>
                <div class="relative inline-block">
                  <button 
                    @click="showDropdown = !showDropdown"
                    class="flex items-center gap-2 px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
                  >
                    Options
                    <ChevronDown class="h-4 w-4" />
                  </button>
                  <div 
                    v-show="showDropdown"
                    class="absolute top-full left-0 mt-1 w-48 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg z-10"
                  >
                    <a href="#" class="block px-4 py-2 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 first:rounded-t-lg">Option 1</a>
                    <a href="#" class="block px-4 py-2 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700">Option 2</a>
                    <a href="#" class="block px-4 py-2 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 last:rounded-b-lg">Option 3</a>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- Colors & Spacing -->
        <section v-show="activeTab === 'colors'" class="space-y-6">
          
          <!-- Color Palette -->
          <div class="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
            <h2 class="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">Color Palette</h2>
            
            <div class="space-y-6">
              <div>
                <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">Primary Colors</h3>
                <div class="grid grid-cols-6 gap-2">
                  <div class="space-y-1">
                    <div class="h-12 w-full bg-primary-50 rounded"></div>
                    <code class="text-xs">50</code>
                  </div>
                  <div class="space-y-1">
                    <div class="h-12 w-full bg-primary-100 rounded"></div>
                    <code class="text-xs">100</code>
                  </div>
                  <div class="space-y-1">
                    <div class="h-12 w-full bg-primary-200 rounded"></div>
                    <code class="text-xs">200</code>
                  </div>
                  <div class="space-y-1">
                    <div class="h-12 w-full bg-primary-400 rounded"></div>
                    <code class="text-xs">400</code>
                  </div>
                  <div class="space-y-1">
                    <div class="h-12 w-full bg-primary-600 rounded"></div>
                    <code class="text-xs">600</code>
                  </div>
                  <div class="space-y-1">
                    <div class="h-12 w-full bg-primary-800 rounded"></div>
                    <code class="text-xs">800</code>
                  </div>
                </div>
              </div>

              <div>
                <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">Gray Scale</h3>
                <div class="grid grid-cols-6 gap-2">
                  <div class="space-y-1">
                    <div class="h-12 w-full bg-gray-50 dark:bg-gray-900 rounded border"></div>
                    <code class="text-xs">50/900</code>
                  </div>
                  <div class="space-y-1">
                    <div class="h-12 w-full bg-gray-100 dark:bg-gray-800 rounded"></div>
                    <code class="text-xs">100/800</code>
                  </div>
                  <div class="space-y-1">
                    <div class="h-12 w-full bg-gray-200 dark:bg-gray-700 rounded"></div>
                    <code class="text-xs">200/700</code>
                  </div>
                  <div class="space-y-1">
                    <div class="h-12 w-full bg-gray-400 dark:bg-gray-500 rounded"></div>
                    <code class="text-xs">400/500</code>
                  </div>
                  <div class="space-y-1">
                    <div class="h-12 w-full bg-gray-600 dark:bg-gray-300 rounded"></div>
                    <code class="text-xs">600/300</code>
                  </div>
                  <div class="space-y-1">
                    <div class="h-12 w-full bg-gray-800 dark:bg-gray-100 rounded"></div>
                    <code class="text-xs">800/100</code>
                  </div>
                </div>
              </div>

              <div>
                <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">Status Colors</h3>
                <div class="grid grid-cols-4 gap-4">
                  <div class="space-y-2">
                    <div class="h-12 w-full bg-green-500 rounded"></div>
                    <span class="text-xs text-gray-600 dark:text-gray-400">Success</span>
                  </div>
                  <div class="space-y-2">
                    <div class="h-12 w-full bg-red-500 rounded"></div>
                    <span class="text-xs text-gray-600 dark:text-gray-400">Error</span>
                  </div>
                  <div class="space-y-2">
                    <div class="h-12 w-full bg-amber-500 rounded"></div>
                    <span class="text-xs text-gray-600 dark:text-gray-400">Warning</span>
                  </div>
                  <div class="space-y-2">
                    <div class="h-12 w-full bg-blue-500 rounded"></div>
                    <span class="text-xs text-gray-600 dark:text-gray-400">Info</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- Spacing Examples -->
          <div class="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
            <h2 class="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">Spacing Scale</h2>
            <div class="space-y-4">
              <div class="flex items-center gap-4">
                <code class="text-sm w-12">p-1</code>
                <div class="p-1 bg-primary-100 dark:bg-primary-900/50">
                  <div class="bg-primary-200 dark:bg-primary-800 px-2 py-1 text-xs">4px</div>
                </div>
              </div>
              <div class="flex items-center gap-4">
                <code class="text-sm w-12">p-2</code>
                <div class="p-2 bg-primary-100 dark:bg-primary-900/50">
                  <div class="bg-primary-200 dark:bg-primary-800 px-2 py-1 text-xs">8px</div>
                </div>
              </div>
              <div class="flex items-center gap-4">
                <code class="text-sm w-12">p-4</code>
                <div class="p-4 bg-primary-100 dark:bg-primary-900/50">
                  <div class="bg-primary-200 dark:bg-primary-800 px-2 py-1 text-xs">16px</div>
                </div>
              </div>
              <div class="flex items-center gap-4">
                <code class="text-sm w-12">p-6</code>
                <div class="p-6 bg-primary-100 dark:bg-primary-900/50">
                  <div class="bg-primary-200 dark:bg-primary-800 px-2 py-1 text-xs">24px</div>
                </div>
              </div>
              <div class="flex items-center gap-4">
                <code class="text-sm w-12">p-8</code>
                <div class="p-8 bg-primary-100 dark:bg-primary-900/50">
                  <div class="bg-primary-200 dark:bg-primary-800 px-2 py-1 text-xs">32px</div>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>

    <!-- Modal Component -->
    <CommonAlertDialog
      :show="showModal"
      type="info"
      title="Sample Modal"
      message="This is a sample modal dialog using the existing AlertDialog component."
      button-text="Close"
      @close="showModal = false"
    />
  </div>
</template>

<script setup lang="ts">
import { 
  Plus, Download, Trash2, Settings, Search, Users, TrendingUp, 
  AlertTriangle, CheckCircle2, Info, AlertCircle, X, ChevronDown
} from 'lucide-vue-next'

// Tab navigation
const activeTab = ref('typography')
const tabs = [
  { id: 'typography', name: 'Typography' },
  { id: 'buttons', name: 'Buttons' },
  { id: 'forms', name: 'Forms' },
  { id: 'cards', name: 'Cards' },
  { id: 'alerts', name: 'Alerts' },
  { id: 'layout', name: 'Layout' },
  { id: 'interactive', name: 'Interactive' },
  { id: 'colors', name: 'Colors' }
]

// Interactive states
const showModal = ref(false)
const toggleState = ref(false)
const progress = ref(65)
const showDropdown = ref(false)

const updateProgress = () => {
  progress.value = Math.floor(Math.random() * 100)
}

// Close dropdown when clicking outside
onMounted(() => {
  document.addEventListener('click', (e) => {
    if (!e.target?.closest('.relative')) {
      showDropdown.value = false
    }
  })
})
</script>

<style scoped>
/* Custom styles for better visual examples */
.transition-all {
  transition-property: all;
  transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
  transition-duration: 150ms;
}
</style>