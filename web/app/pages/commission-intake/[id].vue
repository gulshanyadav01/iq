<template>
  <div class="p-8">
    <div class="max-w-7xl mx-auto space-y-6">
      <!-- Back + Header -->
      <div class="flex items-center gap-4">
        <button
          class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500 transition"
          @click="router.push('/commission-intake')"
        >
          <UIcon name="i-lucide-arrow-left" class="w-4 h-4" />
        </button>
        <div class="flex-1 min-w-0">
          <h1 class="text-xl font-semibold tracking-tight text-gray-900 dark:text-white truncate">
            {{ doc?.original_filename || 'Loading…' }}
          </h1>
          <div class="flex items-center gap-2 mt-1">
            <span v-if="doc?.provider" class="text-xs text-gray-500 dark:text-gray-400">{{ doc.provider }}</span>
            <span v-if="doc?.source_type" class="px-1.5 py-0.5 rounded text-[10px] font-medium bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 uppercase">{{ doc.source_type }}</span>
            <span v-if="doc?.llm_provider" class="px-1.5 py-0.5 rounded text-[10px] font-medium bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400">{{ doc.llm_provider }}</span>
          </div>
        </div>
        <!-- Action buttons: Re-index / Extract -->
        <div v-if="doc && !isInFlight(doc.status)" class="flex items-center gap-2">
          <button
            :disabled="actionLoading !== null"
            class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-600 dark:text-gray-400 transition disabled:opacity-50 disabled:cursor-not-allowed"
            @click="triggerIndex"
          >
            <UIcon
              name="i-lucide-scan-text"
              class="w-3.5 h-3.5"
              :class="{ 'animate-spin': actionLoading === 'index' }"
            />
            Re-index
          </button>
          <button
            v-if="doc.indexed_content"
            :disabled="actionLoading !== null"
            class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-600 dark:text-gray-400 transition disabled:opacity-50 disabled:cursor-not-allowed"
            @click="triggerExtract"
          >
            <UIcon
              name="i-lucide-cpu"
              class="w-3.5 h-3.5"
              :class="{ 'animate-spin': actionLoading === 'extract' }"
            />
            Extract
          </button>
        </div>
        <div class="flex flex-col items-end gap-1">
          <span v-if="doc" :class="['inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium', getStatusBadgeClass(doc.status)]">
            <span v-if="isInFlight(doc.status)" class="w-1.5 h-1.5 rounded-full bg-current animate-pulse" />
            {{ formatStatus(doc.status) }}
          </span>
          <span
            v-if="doc?.status === 'indexing' && doc.index_progress?.detail"
            class="text-[10px] text-gray-400 dark:text-gray-500 max-w-xs text-right truncate"
          >
            {{ doc.index_progress.detail }}
          </span>
        </div>
      </div>

      <!-- Action error -->
      <div v-if="actionError" class="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-400 px-4 py-2 rounded-lg text-xs">
        {{ actionError }}
      </div>

      <!-- Loading -->
      <div v-if="!doc && !loadError" class="glass rounded-2xl p-12 flex items-center justify-center gap-2 text-gray-500">
        <UIcon name="i-lucide-loader-circle" class="animate-spin" />
        Loading…
      </div>

      <!-- Load error -->
      <div v-else-if="loadError" class="glass rounded-2xl p-8 text-center text-sm text-red-600 dark:text-red-400">
        Failed to load document
      </div>

      <!-- Main layout: timeline + content -->
      <div v-else-if="doc" class="flex gap-6 items-start">

        <!-- Left: Pipeline timeline -->
        <div class="w-48 flex-shrink-0 glass rounded-2xl p-4 space-y-1">
          <p class="text-[10px] font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500 px-2 pb-2">Pipeline</p>
          <button
            v-for="stage in stages"
            :key="stage.id"
            :disabled="!stage.available"
            :class="[
              'w-full flex items-center gap-2.5 px-2 py-2 rounded-lg text-xs font-medium text-left transition',
              activeStage === stage.id
                ? 'bg-primary-500/10 text-primary-700 dark:text-primary-300'
                : stage.available
                  ? 'text-gray-600 dark:text-gray-400 hover:bg-gray-100/60 dark:hover:bg-white/[0.04] hover:text-gray-900 dark:hover:text-gray-200'
                  : 'text-gray-300 dark:text-gray-600 cursor-not-allowed'
            ]"
            @click="stage.available && (activeStage = stage.id)"
          >
            <!-- Stage icon -->
            <span class="flex-shrink-0 w-5 h-5 flex items-center justify-center">
              <UIcon v-if="stage.status === 'done'" name="i-lucide-check-circle-2" class="w-4 h-4 text-green-500" />
              <UIcon v-else-if="stage.status === 'active'" name="i-lucide-loader-circle" class="w-4 h-4 text-blue-500 animate-spin" />
              <UIcon v-else-if="stage.status === 'failed'" name="i-lucide-x-circle" class="w-4 h-4 text-red-500" />
              <span v-else class="w-3 h-3 rounded-full border-2 border-gray-300 dark:border-gray-600" />
            </span>
            <span class="truncate">{{ stage.label }}</span>
          </button>
        </div>

        <!-- Right: Stage content -->
        <div class="flex-1 min-w-0 glass rounded-2xl overflow-hidden">

          <!-- ── Indexed Content ── -->
          <div v-if="activeStage === 'indexed'">
            <div class="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
              <h2 class="text-sm font-semibold text-gray-900 dark:text-white">Indexed Content</h2>
              <p class="text-xs text-gray-500 mt-0.5">Extracted and cached from source document</p>
            </div>
            <div v-if="doc.indexed_content" class="p-6 space-y-4">
              <!-- Excel: sheet list -->
              <template v-if="doc.indexed_content.source_type === 'excel'">
                <div v-for="(content, sheetName) in doc.indexed_content.sheet_contents" :key="sheetName" class="space-y-2">
                  <div class="flex items-center gap-2">
                    <span class="text-sm font-medium text-gray-900 dark:text-white">{{ sheetName }}</span>
                    <span :class="['px-1.5 py-0.5 rounded text-[10px] font-medium uppercase', getSheetClassBadge(sheetName, doc.indexed_content)]">
                      {{ getSheetClassification(sheetName, doc.indexed_content) }}
                    </span>
                  </div>
                  <details class="group">
                    <summary class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 cursor-pointer select-none">
                      Show content
                    </summary>
                    <pre class="mt-2 text-xs bg-gray-50 dark:bg-gray-900 p-4 rounded-lg overflow-auto max-h-64 leading-relaxed whitespace-pre-wrap">{{ (content as string).slice(0, 3000) }}{{ (content as string).length > 3000 ? '\n…(truncated)' : '' }}</pre>
                  </details>
                </div>
              </template>
              <!-- Email -->
              <template v-else-if="doc.indexed_content.source_type === 'email'">
                <div v-if="doc.indexed_content.email_context" class="space-y-1">
                  <p class="text-xs font-semibold text-gray-500 uppercase tracking-widest">Email Headers</p>
                  <pre class="text-xs bg-gray-50 dark:bg-gray-900 p-4 rounded-lg whitespace-pre-wrap">{{ doc.indexed_content.email_context }}</pre>
                </div>
                <div class="space-y-1">
                  <p class="text-xs font-semibold text-gray-500 uppercase tracking-widest">Body</p>
                  <details class="group">
                    <summary class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 cursor-pointer select-none">Show content</summary>
                    <pre class="mt-2 text-xs bg-gray-50 dark:bg-gray-900 p-4 rounded-lg overflow-auto max-h-96 leading-relaxed whitespace-pre-wrap">{{ doc.indexed_content.primary_content.slice(0, 5000) }}{{ doc.indexed_content.primary_content.length > 5000 ? '\n…(truncated)' : '' }}</pre>
                  </details>
                </div>
                <!-- Excel attachments extracted from email -->
                <template v-if="doc.indexed_content.sheet_contents && Object.keys(doc.indexed_content.sheet_contents).length">
                  <div class="pt-2 border-t border-gray-100 dark:border-gray-800">
                    <p class="text-xs font-semibold text-gray-500 uppercase tracking-widest mb-3">Excel Attachments</p>
                    <div v-for="(content, sheetName) in doc.indexed_content.sheet_contents" :key="sheetName" class="space-y-2">
                      <div class="flex items-center gap-2">
                        <span class="text-sm font-medium text-gray-900 dark:text-white">{{ sheetName }}</span>
                        <span :class="['px-1.5 py-0.5 rounded text-[10px] font-medium uppercase', getSheetClassBadge(sheetName, doc.indexed_content)]">
                          {{ getSheetClassification(sheetName, doc.indexed_content) }}
                        </span>
                      </div>
                      <details class="group">
                        <summary class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 cursor-pointer select-none">
                          Show content
                        </summary>
                        <pre class="mt-2 text-xs bg-gray-50 dark:bg-gray-900 p-4 rounded-lg overflow-auto max-h-64 leading-relaxed whitespace-pre-wrap">{{ (content as string).slice(0, 3000) }}{{ (content as string).length > 3000 ? '\n…(truncated)' : '' }}</pre>
                      </details>
                    </div>
                  </div>
                </template>
              </template>
              <!-- PDF -->
              <template v-else>
                <details class="group">
                  <summary class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 cursor-pointer select-none">Show extracted text</summary>
                  <pre class="mt-2 text-xs bg-gray-50 dark:bg-gray-900 p-4 rounded-lg overflow-auto max-h-96 leading-relaxed whitespace-pre-wrap">{{ doc.indexed_content.primary_content.slice(0, 5000) }}{{ doc.indexed_content.primary_content.length > 5000 ? '\n…(truncated)' : '' }}</pre>
                </details>
              </template>

              <div class="pt-2 text-xs text-gray-400">Indexer v{{ doc.indexed_content.indexer_version }}</div>
            </div>
            <div v-else class="p-6 text-sm text-gray-500">
              <span v-if="isInFlight(doc.status)">Indexing in progress…</span>
              <span v-else>No indexed content available.</span>
            </div>
          </div>

          <!-- ── Extraction Pass ── -->
          <div v-else-if="activeStage === 'pass1' || activeStage === 'pass2' || activeStage === 'pass3'">
            <!-- Sheet tab bar (shown when multiple sheets exist) -->
            <div v-if="currentPassSheets.length > 1" class="px-6 pt-4 pb-0 border-b border-gray-200 dark:border-gray-700 flex items-center gap-1 overflow-x-auto">
              <button
                v-for="sheet in currentPassSheets"
                :key="sheet.key"
                :class="[
                  'flex items-center gap-1.5 px-3 py-2 text-xs font-medium whitespace-nowrap border-b-2 transition',
                  activePassSheetKey === sheet.key
                    ? 'border-primary-500 text-primary-600 dark:text-primary-400'
                    : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'
                ]"
                @click="activePassSheetKey = sheet.key"
              >
                <!-- per-sheet status indicator -->
                <UIcon
                  v-if="sheetPassStatus(activeStage === 'pass1' ? 1 : activeStage === 'pass2' ? 2 : 3, sheet.attachment, sheet.sheet) === 'completed'"
                  name="i-lucide-check-circle-2" class="w-3 h-3 text-green-500"
                />
                <UIcon
                  v-else-if="sheetPassStatus(activeStage === 'pass1' ? 1 : activeStage === 'pass2' ? 2 : 3, sheet.attachment, sheet.sheet) === 'failed'"
                  name="i-lucide-x-circle" class="w-3 h-3 text-red-500"
                />
                <UIcon
                  v-else-if="sheetPassStatus(activeStage === 'pass1' ? 1 : activeStage === 'pass2' ? 2 : 3, sheet.attachment, sheet.sheet) === 'running'"
                  name="i-lucide-loader-circle" class="w-3 h-3 text-blue-500 animate-spin"
                />
                <span
                  v-else
                  class="w-2.5 h-2.5 rounded-full border-2 border-gray-300 dark:border-gray-600"
                />
                {{ sheet.label }}
              </button>
            </div>

            <template v-if="currentPass">
              <div class="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between" :class="{ 'border-t-0': currentPassSheets.length > 1 }">
                <div>
                  <h2 class="text-sm font-semibold text-gray-900 dark:text-white">{{ passLabels[activeStage] }}</h2>
                  <p class="text-xs text-gray-500 mt-0.5">
                    {{ currentPass.llm_model_used || doc.llm_provider }}
                    <span v-if="currentPass.duration_seconds"> · {{ currentPass.duration_seconds.toFixed(1) }}s</span>
                    <span v-if="currentPass.status && currentPass.status !== 'completed'" :class="['ml-2 px-1.5 py-0.5 rounded text-[10px] font-medium uppercase', getPassStatusBadge(currentPass.status)]">{{ currentPass.status }}</span>
                  </p>
                </div>
                <div class="flex items-center gap-2">
                  <button
                    v-if="!isInFlight(doc.status) && currentPass.status !== 'running'"
                    :disabled="actionLoading !== null"
                    class="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition disabled:opacity-50 disabled:cursor-not-allowed"
                    @click="triggerRerunPass(currentPass.pass_number, currentPass.sheet_name, currentPass.attachment_name)"
                  >
                    <UIcon
                      name="i-lucide-refresh-cw"
                      class="w-3.5 h-3.5"
                      :class="{ 'animate-spin': actionLoading === `rerun-pass-${currentPass.pass_number}` }"
                    />
                    Rerun
                  </button>
                  <button v-if="currentPass.result" class="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition" @click="copyJson(currentPass.result)">
                    <UIcon :name="copied ? 'i-lucide-check' : 'i-lucide-copy'" class="w-3.5 h-3.5" />
                    {{ copied ? 'Copied!' : 'Copy' }}
                  </button>
                </div>
              </div>
              <div class="p-6">
                <div v-if="currentPass.status === 'failed'" class="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg px-4 py-3 text-xs text-red-700 dark:text-red-400">
                  {{ currentPass.error || 'Pass failed' }}
                </div>
                <div v-else-if="currentPass.status === 'pending'" class="text-sm text-gray-500">
                  Waiting to start…
                </div>
                <div v-else-if="currentPass.status === 'running'" class="flex items-center gap-2 text-sm text-gray-500">
                  <UIcon name="i-lucide-loader-circle" class="animate-spin w-4 h-4" />
                  Running…
                </div>
                <pre v-else class="text-xs bg-gray-50 dark:bg-gray-900 p-4 rounded-lg overflow-auto max-h-[60vh] leading-relaxed whitespace-pre-wrap">{{ JSON.stringify(currentPass.result, null, 2) }}</pre>
              </div>
            </template>
            <div v-else class="p-6 text-sm text-gray-500">
              <span v-if="isInFlight(doc.status)">Extraction in progress…</span>
              <span v-else>No data for this pass.</span>
            </div>
          </div>

          <!-- ── CICS Result ── -->
          <div v-else-if="activeStage === 'cics'">
            <div v-if="doc.cics_result">
              <!-- Sub-tabs -->
              <div class="px-6 pt-4 pb-0 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
                <div class="flex gap-4 text-xs font-medium">
                  <button
                    v-for="t in cicsTabs"
                    :key="t.id"
                    :class="['pb-3 border-b-2 transition', activeCicsTab === t.id ? 'border-primary-500 text-primary-600 dark:text-primary-400' : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300']"
                    @click="activeCicsTab = t.id"
                  >
                    {{ t.label }}
                  </button>
                </div>
                <div class="flex items-center gap-2 pb-3">
                  <button
                    v-if="!isInFlight(doc.status)"
                    :disabled="actionLoading !== null"
                    class="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition disabled:opacity-50 disabled:cursor-not-allowed"
                    @click="triggerTransform"
                  >
                    <UIcon
                      name="i-lucide-refresh-cw"
                      class="w-3.5 h-3.5"
                      :class="{ 'animate-spin': actionLoading === 'transform' }"
                    />
                    Re-transform
                  </button>
                  <button class="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition" @click="copyJson(doc.cics_result!.cics_doc)">
                    <UIcon :name="copied ? 'i-lucide-check' : 'i-lucide-copy'" class="w-3.5 h-3.5" />
                    {{ copied ? 'Copied!' : 'Copy' }}
                  </button>
                  <button class="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition" @click="downloadJson">
                    <UIcon name="i-lucide-download" class="w-3.5 h-3.5" />
                    Download
                  </button>
                </div>
              </div>

              <!-- Warnings / errors banner -->
              <div v-if="doc.cics_result.errors.length" class="mx-6 mt-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg px-4 py-2 text-xs text-red-700 dark:text-red-400">
                {{ doc.cics_result.errors.length }} transform error(s): {{ doc.cics_result.errors[0] }}
              </div>
              <div v-else-if="doc.cics_result.warnings.length" class="mx-6 mt-4 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded-lg px-4 py-2 text-xs text-yellow-700 dark:text-yellow-400">
                {{ doc.cics_result.warnings.length }} transform warning(s)
              </div>

              <!-- Summary tab -->
              <div v-if="activeCicsTab === 'summary'" class="p-6 space-y-6">
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-4">
                  <div v-for="metric in cicsMetrics" :key="metric.label" class="bg-gray-50 dark:bg-gray-800/50 rounded-xl p-4">
                    <p class="text-xs text-gray-500 dark:text-gray-400">{{ metric.label }}</p>
                    <p class="text-lg font-semibold text-gray-900 dark:text-white mt-1">{{ metric.value }}</p>
                  </div>
                </div>
                <div class="space-y-2">
                  <p class="text-xs font-semibold text-gray-500 uppercase tracking-widest">Document Metadata</p>
                  <table class="w-full text-sm">
                    <tbody class="divide-y divide-gray-100 dark:divide-gray-800">
                      <tr v-for="row in cicsMetadataRows" :key="row.key">
                        <td class="py-2 pr-4 text-xs text-gray-500 dark:text-gray-400 font-mono w-1/3">{{ row.key }}</td>
                        <td class="py-2 text-sm text-gray-900 dark:text-gray-100">{{ row.value || '—' }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>

              <!-- Products tab -->
              <div v-else-if="activeCicsTab === 'products'" class="p-6 space-y-4 overflow-auto max-h-[60vh]">
                <div v-if="!cicsProducts.length" class="text-sm text-gray-500">No products found.</div>
                <details
                  v-for="product in cicsProducts"
                  :key="product.product_code"
                  class="group bg-gray-50 dark:bg-gray-800/50 rounded-xl overflow-hidden"
                  open
                >
                  <summary class="flex items-center justify-between px-4 py-3 cursor-pointer select-none hover:bg-gray-100 dark:hover:bg-gray-700/50 transition">
                    <div class="flex items-center gap-2">
                      <span class="text-sm font-medium text-gray-900 dark:text-white">{{ product.product_name }}</span>
                      <span class="px-1.5 py-0.5 rounded text-[10px] font-medium bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-400">{{ product.product_category }}</span>
                    </div>
                    <span class="text-xs text-gray-400">{{ product.commission_rules?.length || 0 }} rules</span>
                  </summary>
                  <div class="px-4 pb-4 overflow-auto">
                    <table v-if="product.commission_rules?.length" class="w-full text-xs mt-2">
                      <thead>
                        <tr class="text-left text-gray-400 dark:text-gray-500 border-b border-gray-200 dark:border-gray-700">
                          <th class="pb-2 pr-4 font-semibold">Rule ID</th>
                          <th class="pb-2 pr-4 font-semibold">Type</th>
                          <th class="pb-2 pr-4 font-semibold">Value</th>
                          <th class="pb-2 pr-4 font-semibold">Conditions</th>
                          <th class="pb-2 font-semibold">Priority</th>
                        </tr>
                      </thead>
                      <tbody class="divide-y divide-gray-100 dark:divide-gray-800">
                        <tr v-for="rule in product.commission_rules" :key="rule.rule_id" class="align-top">
                          <td class="py-2 pr-4 font-mono text-gray-500">{{ rule.rule_id }}</td>
                          <td class="py-2 pr-4 text-gray-700 dark:text-gray-300">{{ rule.outcome?.commission_type || '—' }}</td>
                          <td class="py-2 pr-4 text-gray-900 dark:text-gray-100 font-medium">{{ formatCommissionValue(rule.outcome) }}</td>
                          <td class="py-2 pr-4 text-gray-500 dark:text-gray-400 max-w-xs">{{ formatConditions(rule.conditions) }}</td>
                          <td class="py-2 text-gray-500">{{ rule.priority ?? '—' }}</td>
                        </tr>
                      </tbody>
                    </table>
                    <p v-else class="text-xs text-gray-400 mt-2">No rules</p>
                  </div>
                </details>
              </div>

              <!-- Raw JSON tab -->
              <div v-else-if="activeCicsTab === 'json'" class="p-6">
                <pre class="text-xs bg-gray-50 dark:bg-gray-900 p-4 rounded-lg overflow-auto max-h-[60vh] leading-relaxed whitespace-pre-wrap">{{ JSON.stringify(doc.cics_result.cics_doc, null, 2) }}</pre>
              </div>
            </div>
            <div v-else class="p-6 text-sm text-gray-500">
              <span v-if="isInFlight(doc.status)">Extraction in progress…</span>
              <span v-else>No CICS result available.</span>
            </div>
          </div>

          <!-- ── Validation ── -->
          <div v-else-if="activeStage === 'validation'">
            <div v-if="doc.validation_result">
              <div class="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
                <h2 class="text-sm font-semibold text-gray-900 dark:text-white">Validation</h2>
                <button
                  v-if="!isInFlight(doc.status)"
                  :disabled="actionLoading !== null"
                  class="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition disabled:opacity-50 disabled:cursor-not-allowed"
                  @click="triggerValidate"
                >
                  <UIcon
                    name="i-lucide-refresh-cw"
                    class="w-3.5 h-3.5"
                    :class="{ 'animate-spin': actionLoading === 'validate' }"
                  />
                  Re-validate
                </button>
              </div>
              <div class="p-6 space-y-6">
                <!-- Overall banner -->
                <div :class="['rounded-xl px-4 py-3 text-sm font-medium flex items-center gap-2', validationBannerClass]">
                  <UIcon :name="validationBannerIcon" class="w-4 h-4 flex-shrink-0" />
                  {{ validationBannerText }}
                </div>

                <!-- Schema issues -->
                <div v-if="doc.validation_result.schema_issues.length" class="space-y-2">
                  <p class="text-xs font-semibold text-gray-500 uppercase tracking-widest">Schema Issues</p>
                  <table class="w-full text-xs">
                    <thead>
                      <tr class="text-left text-gray-400 border-b border-gray-100 dark:border-gray-800">
                        <th class="pb-2 pr-4 font-semibold">Severity</th>
                        <th class="pb-2 pr-4 font-semibold">Category</th>
                        <th class="pb-2 pr-4 font-semibold">Field</th>
                        <th class="pb-2 font-semibold">Message</th>
                      </tr>
                    </thead>
                    <tbody class="divide-y divide-gray-100 dark:divide-gray-800">
                      <tr v-for="(issue, i) in doc.validation_result.schema_issues" :key="i" class="align-top">
                        <td class="py-2 pr-4">
                          <span :class="['px-1.5 py-0.5 rounded text-[10px] font-medium uppercase', getSeverityBadge(issue.severity)]">{{ issue.severity }}</span>
                        </td>
                        <td class="py-2 pr-4 text-gray-600 dark:text-gray-400">{{ issue.category }}</td>
                        <td class="py-2 pr-4 font-mono text-gray-500 max-w-[10rem] truncate" :title="issue.field_path">{{ issue.field_path || '—' }}</td>
                        <td class="py-2 text-gray-700 dark:text-gray-300">{{ issue.message }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>

                <!-- Overlap issues -->
                <div v-if="doc.validation_result.overlap_issues.length" class="space-y-2">
                  <p class="text-xs font-semibold text-gray-500 uppercase tracking-widest">Rule Overlap Issues</p>
                  <table class="w-full text-xs">
                    <thead>
                      <tr class="text-left text-gray-400 border-b border-gray-100 dark:border-gray-800">
                        <th class="pb-2 pr-4 font-semibold">Severity</th>
                        <th class="pb-2 pr-4 font-semibold">Type</th>
                        <th class="pb-2 pr-4 font-semibold">Product</th>
                        <th class="pb-2 font-semibold">Rules</th>
                      </tr>
                    </thead>
                    <tbody class="divide-y divide-gray-100 dark:divide-gray-800">
                      <tr v-for="(overlap, i) in doc.validation_result.overlap_issues" :key="i" class="align-top">
                        <td class="py-2 pr-4">
                          <span :class="['px-1.5 py-0.5 rounded text-[10px] font-medium uppercase', getSeverityBadge(overlap.severity)]">{{ overlap.severity }}</span>
                        </td>
                        <td class="py-2 pr-4 text-gray-600 dark:text-gray-400">{{ overlap.overlap_type }}</td>
                        <td class="py-2 pr-4 font-mono text-gray-500">{{ overlap.product_code }}</td>
                        <td class="py-2 text-gray-700 dark:text-gray-300">{{ overlap.rule1_id }} vs {{ overlap.rule2_id }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>

                <p v-if="!doc.validation_result.schema_issues.length && !doc.validation_result.overlap_issues.length" class="text-sm text-gray-500">No issues found.</p>
              </div>
            </div>
            <div v-else class="p-6 text-sm text-gray-500">
              <span v-if="isInFlight(doc.status)">Validation pending…</span>
              <span v-else>No validation result available.</span>
            </div>
          </div>

          <!-- Failed stage detail -->
          <div v-if="doc.status === 'failed' && doc.error && !activeStage" class="p-6">
            <div class="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg px-4 py-3 text-sm text-red-700 dark:text-red-400">
              {{ doc.error }}
            </div>
          </div>
        </div>
      </div>

      <!-- Failure banner when doc failed -->
      <div v-if="doc?.status === 'failed' && doc.error" class="glass rounded-2xl px-6 py-4 bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-800 text-sm text-red-700 dark:text-red-400">
        <span class="font-medium">Pipeline failed:</span> {{ doc.error }}
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useCommissionIntakeDetail } from '~/composables/useCommissionIntakeDetail'

definePageMeta({ layout: 'default', middleware: 'auth' })

const router = useRouter()

const passLabels: Record<string, string> = {
  pass1: 'Pass 1: Metadata',
  pass2: 'Pass 2: Master Data',
  pass3: 'Pass 3: Products & Rules',
}

const cicsTabs = [
  { id: 'summary' as const, label: 'Summary' },
  { id: 'products' as const, label: 'Products' },
  { id: 'json' as const, label: 'Raw JSON' },
]

const {
  doc, loadError, activeStage, copied, activeCicsTab, actionLoading, actionError, activePassSheetKey,
  stages, currentPassSheets, currentPass,
  cicsMetrics, cicsMetadataRows, cicsProducts,
  validationBannerClass, validationBannerIcon, validationBannerText,
  isInFlight, getStatusBadgeClass, formatStatus, getPassStatusBadge, getSeverityBadge,
  getSheetClassification, getSheetClassBadge, formatCommissionValue, formatConditions,
  sheetPassStatus, copyJson, downloadJson,
  triggerIndex, triggerExtract, triggerRerunPass, triggerTransform, triggerValidate,
} = useCommissionIntakeDetail()
</script>
