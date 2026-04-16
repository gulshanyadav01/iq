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
                <template v-if="doc">
                  <UIcon
                    v-if="doc.extraction_passes.find(p => p.pass_number === (activeStage === 'pass1' ? 1 : activeStage === 'pass2' ? 2 : 3) && p.attachment_name === sheet.attachment && p.sheet_name === sheet.sheet)?.status === 'completed'"
                    name="i-lucide-check-circle-2" class="w-3 h-3 text-green-500"
                  />
                  <UIcon
                    v-else-if="doc.extraction_passes.find(p => p.pass_number === (activeStage === 'pass1' ? 1 : activeStage === 'pass2' ? 2 : 3) && p.attachment_name === sheet.attachment && p.sheet_name === sheet.sheet)?.status === 'failed'"
                    name="i-lucide-x-circle" class="w-3 h-3 text-red-500"
                  />
                  <UIcon
                    v-else-if="doc.extraction_passes.find(p => p.pass_number === (activeStage === 'pass1' ? 1 : activeStage === 'pass2' ? 2 : 3) && p.attachment_name === sheet.attachment && p.sheet_name === sheet.sheet)?.status === 'running'"
                    name="i-lucide-loader-circle" class="w-3 h-3 text-blue-500 animate-spin"
                  />
                  <span
                    v-else
                    class="w-2.5 h-2.5 rounded-full border-2 border-gray-300 dark:border-gray-600"
                  />
                </template>
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
                <button v-if="currentPass.result" class="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition" @click="copyJson(currentPass.result)">
                  <UIcon :name="copied ? 'i-lucide-check' : 'i-lucide-copy'" class="w-3.5 h-3.5" />
                  {{ copied ? 'Copied!' : 'Copy' }}
                </button>
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
              <div class="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
                <h2 class="text-sm font-semibold text-gray-900 dark:text-white">Validation</h2>
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
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

// ── Types ──────────────────────────────────────────────────────────────────

interface ValidationIssue {
  severity: string
  category: string
  field_path: string
  message: string
  suggestion: string
}

interface OverlapIssue {
  severity: string
  overlap_type: string
  product_code: string
  rule1_id: string
  rule2_id: string
  description: string
}

interface ValidationResult {
  is_valid: boolean
  has_critical: boolean
  has_high: boolean
  schema_issues: ValidationIssue[]
  overlap_issues: OverlapIssue[]
}

interface CICSResult {
  id: string
  cics_doc: Record<string, unknown>
  warnings: string[]
  errors: string[]
  duration_seconds: number
  sheet_results: unknown[]
}

interface IndexedContent {
  source_type: string
  primary_content: string
  email_context: string
  sheet_contents: Record<string, string>
  sheet_metadata: Record<string, unknown>
  indexer_version: string
}

interface ExtractionPass {
  pass_number: 1 | 2 | 3
  sheet_name: string
  attachment_name: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  result: Record<string, unknown> | null
  llm_model_used: string
  duration_seconds: number
  error: string
}

interface IndexProgress {
  step: string
  detail: string
}

interface CommissionDocument {
  id: string
  status: 'initiated' | 'indexing' | 'indexed' | 'extracting' | 'completed' | 'failed'
  processing_mode: 'full' | 'index_only' | 'extract_only'
  index_progress: IndexProgress | null
  original_filename: string
  source_type: string
  provider: string
  llm_provider: string
  llm_model: string
  excel_mode: string
  error: string | null
  indexed_content: IndexedContent | null
  extraction_passes: ExtractionPass[]
  cics_result: CICSResult | null
  validation_result: ValidationResult | null
  created_at: string
  updated_at: string
}

// ── Setup ──────────────────────────────────────────────────────────────────

definePageMeta({ layout: 'default', middleware: 'auth' })

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const { getBaseUrl } = useBaseUrl()

const doc = ref<CommissionDocument | null>(null)
const loadError = ref(false)
const activeStage = ref<string>('indexed')
const copied = ref(false)
const activeCicsTab = ref<'summary' | 'products' | 'json'>('summary')
const actionLoading = ref<'index' | 'extract' | null>(null)
const actionError = ref<string | null>(null)
// Active sheet key for the pass detail panel: "{attachment_name}||{sheet_name}"
const activePassSheetKey = ref<string>('')

let pollTimer: ReturnType<typeof setInterval> | null = null

const passLabels: Record<string, string> = {
  pass1: 'Pass 1: Metadata',
  pass2: 'Pass 2: Master Data',
  pass3: 'Pass 3: Products & Rules',
}

const cicsTabs: { id: 'summary' | 'products' | 'json'; label: string }[] = [
  { id: 'summary', label: 'Summary' },
  { id: 'products', label: 'Products' },
  { id: 'json', label: 'Raw JSON' },
]

// ── Stage computation ──────────────────────────────────────────────────────

type StageStatus = 'pending' | 'active' | 'done' | 'failed'

interface Stage {
  id: string
  label: string
  status: StageStatus
  available: boolean
}

// Helper: get all ExtractionPass rows for a given pass number
function passesForNum(passNum: number): ExtractionPass[] {
  return (doc.value?.extraction_passes || []).filter(p => p.pass_number === passNum)
}

// Aggregate status across all sheets for a pass number
function passAggregateStatus(passNum: number): StageStatus {
  const passes = passesForNum(passNum)
  if (!passes.length) return 'pending'
  if (passes.every(p => p.status === 'completed')) return 'done'
  if (passes.some(p => p.status === 'running')) return 'active'
  if (passes.some(p => p.status === 'failed') && passes.every(p => p.status === 'failed' || p.status === 'completed')) return 'failed'
  // If document is completed but some passes are still pending (stale pre-created rows),
  // treat as done if at least one pass completed
  if (doc.value?.status === 'completed' && passes.some(p => p.status === 'completed')) return 'done'
  // some pending — still in progress
  return 'active'
}

const stages = computed<Stage[]>(() => {
  if (!doc.value) return []
  const d = doc.value
  const status = d.status
  const failed = status === 'failed'

  const hasIndexed = !!d.indexed_content
  const passes1 = passesForNum(1)
  const passes2 = passesForNum(2)
  const passes3 = passesForNum(3)
  const hasAnyPass1 = passes1.length > 0
  const hasAnyPass2 = passes2.length > 0
  const hasAnyPass3 = passes3.length > 0
  const hasCics = !!d.cics_result
  const hasValidation = !!d.validation_result

  function stageStatus(hasData: boolean, isCurrentStage: boolean): StageStatus {
    if (hasData) return 'done'
    if (isCurrentStage && !failed) return 'active'
    if (failed && !hasData) return 'failed'
    return 'pending'
  }

  const indexingActive = status === 'indexing' || status === 'initiated'
  const extractingActive = status === 'extracting'

  const pass1Status: StageStatus = hasAnyPass1 ? passAggregateStatus(1) : stageStatus(false, extractingActive)
  const pass2Status: StageStatus = hasAnyPass2 ? passAggregateStatus(2) : stageStatus(false, extractingActive && passAggregateStatus(1) === 'done')
  const pass3Status: StageStatus = hasAnyPass3 ? passAggregateStatus(3) : stageStatus(false, extractingActive && passAggregateStatus(2) === 'done')

  return [
    { id: 'indexed', label: 'Indexed', status: stageStatus(hasIndexed, indexingActive), available: hasIndexed },
    { id: 'pass1', label: 'Pass 1', status: pass1Status, available: hasAnyPass1 },
    { id: 'pass2', label: 'Pass 2', status: pass2Status, available: hasAnyPass2 },
    { id: 'pass3', label: 'Pass 3', status: pass3Status, available: hasAnyPass3 },
    { id: 'cics', label: 'CICS Result', status: stageStatus(hasCics, extractingActive && pass3Status === 'done' && !hasCics), available: hasCics },
    { id: 'validation', label: 'Validation', status: stageStatus(hasValidation, status === 'completed' && !hasValidation), available: hasValidation },
  ]
})

// Auto-select the furthest completed stage or first available
watch(stages, (newStages) => {
  const available = newStages.filter(s => s.available)
  if (available.length && !available.find(s => s.id === activeStage.value)) {
    activeStage.value = available[available.length - 1]!.id
  }
}, { immediate: true })

// ── Per-sheet pass data ────────────────────────────────────────────────────

// Unique sheet keys for the current pass stage: "{attachment_name}||{sheet_name}"
const currentPassSheets = computed<{ key: string; label: string; attachment: string; sheet: string }[]>(() => {
  const num = activeStage.value === 'pass1' ? 1 : activeStage.value === 'pass2' ? 2 : activeStage.value === 'pass3' ? 3 : null
  if (num === null) return []
  const passes = passesForNum(num)
  const seen = new Set<string>()
  const result: { key: string; label: string; attachment: string; sheet: string }[] = []
  // Determine if multiple distinct attachments exist — only then prefix the tab with attachment name
  const distinctAttachments = new Set(passes.map(p => p.attachment_name).filter(Boolean))
  const multiAttachment = distinctAttachments.size > 1
  for (const p of passes) {
    const key = `${p.attachment_name}||${p.sheet_name}`
    if (!seen.has(key)) {
      seen.add(key)
      const sheetLabel = p.sheet_name || 'Document'
      const label = (multiAttachment && p.attachment_name) ? `${p.attachment_name} / ${sheetLabel}` : sheetLabel
      result.push({ key, label, attachment: p.attachment_name, sheet: p.sheet_name })
    }
  }
  return result
})

// Currently selected sheet key — auto-set to first sheet when stage changes
watch([activeStage, currentPassSheets], ([stage, sheets]) => {
  if (['pass1', 'pass2', 'pass3'].includes(stage) && sheets.length && !sheets.find(s => s.key === activePassSheetKey.value)) {
    activePassSheetKey.value = sheets[0]!.key
  }
}, { immediate: true })

// The ExtractionPass for the currently selected sheet + active pass number
const currentPass = computed<ExtractionPass | null>(() => {
  const num = activeStage.value === 'pass1' ? 1 : activeStage.value === 'pass2' ? 2 : activeStage.value === 'pass3' ? 3 : null
  if (num === null || !doc.value) return null
  const [attName, sheetName] = activePassSheetKey.value.split('||')
  return doc.value.extraction_passes.find(
    p => p.pass_number === num && p.attachment_name === (attName ?? '') && p.sheet_name === (sheetName ?? '')
  ) || null
})

// ── CICS computed ──────────────────────────────────────────────────────────

const cicsDoc = computed(() => doc.value?.cics_result?.cics_doc as Record<string, unknown> | undefined)

const cicsMetrics = computed(() => {
  const d = cicsDoc.value
  if (!d) return []
  const products = (d.products as unknown[]) || []
  const ruleCount = products.reduce((sum, p: any) => sum + (p.commission_rules?.length || 0), 0)
  return [
    { label: 'Products', value: products.length },
    { label: 'Total Rules', value: ruleCount },
    { label: 'Duration', value: doc.value?.cics_result?.duration_seconds ? `${doc.value.cics_result.duration_seconds.toFixed(1)}s` : '—' },
    { label: 'Schema', value: (d.schema_version as string) || '—' },
  ]
})

const cicsMetadataRows = computed(() => {
  const d = cicsDoc.value
  if (!d) return []
  const meta = d.document_metadata as Record<string, unknown> | undefined
  const provider = d.provider as Record<string, unknown> | undefined
  const period = d.commission_period as Record<string, unknown> | undefined
  return [
    { key: 'document_id', value: meta?.document_id },
    { key: 'provider', value: provider?.name },
    { key: 'period_label', value: (period as any)?.label },
    { key: 'effective_from', value: (period as any)?.effective_from },
    { key: 'effective_to', value: (period as any)?.effective_to },
    { key: 'source_file', value: meta?.source_file },
  ]
})

const cicsProducts = computed(() => {
  const d = cicsDoc.value
  if (!d) return []
  return (d.products as any[]) || []
})

// ── Validation computed ────────────────────────────────────────────────────

const validationBannerClass = computed(() => {
  const vr = doc.value?.validation_result
  if (!vr) return ''
  if (vr.has_critical || vr.has_high) return 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 border border-red-200 dark:border-red-800'
  if (!vr.is_valid) return 'bg-yellow-50 dark:bg-yellow-900/20 text-yellow-700 dark:text-yellow-400 border border-yellow-200 dark:border-yellow-800'
  return 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400 border border-green-200 dark:border-green-800'
})

const validationBannerIcon = computed(() => {
  const vr = doc.value?.validation_result
  if (!vr) return 'i-lucide-circle'
  if (vr.has_critical || vr.has_high) return 'i-lucide-x-circle'
  if (!vr.is_valid) return 'i-lucide-alert-triangle'
  return 'i-lucide-check-circle-2'
})

const validationBannerText = computed(() => {
  const vr = doc.value?.validation_result
  if (!vr) return ''
  if (vr.has_critical) return 'Validation failed — critical issues found'
  if (vr.has_high) return 'Validation failed — high priority issues found'
  if (!vr.is_valid) return 'Validation passed with warnings'
  return 'Validation passed'
})

// ── Helpers ────────────────────────────────────────────────────────────────

function isInFlight(status: string): boolean {
  return ['initiated', 'indexing', 'extracting'].includes(status)
}

function getStatusBadgeClass(status: string): string {
  switch (status) {
    case 'initiated': return 'bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-400'
    case 'indexing': return 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400'
    case 'extracting': return 'bg-indigo-100 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-400'
    case 'completed': return 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'
    case 'failed': return 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400'
    default: return 'bg-gray-100 dark:bg-gray-900/30 text-gray-700 dark:text-gray-400'
  }
}

function formatStatus(status: string): string {
  return status.charAt(0).toUpperCase() + status.slice(1)
}

function getPassStatusBadge(status: string): string {
  switch (status) {
    case 'completed': return 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'
    case 'running': return 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400'
    case 'failed': return 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400'
    default: return 'bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400'
  }
}

function getSeverityBadge(severity: string): string {
  switch (severity?.toUpperCase()) {
    case 'CRITICAL': return 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400'
    case 'HIGH': return 'bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-400'
    case 'MEDIUM': return 'bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-400'
    default: return 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400'
  }
}

function getSheetClassification(sheetName: string, indexed: IndexedContent): string {
  const classifications = (indexed.sheet_metadata as any)?.classifications || {}
  const entry = classifications[sheetName]
  return entry?.classification || entry?.sheet_type || 'unknown'
}

function getSheetClassBadge(sheetName: string, indexed: IndexedContent): string {
  const cls = getSheetClassification(sheetName, indexed)
  if (cls === 'commission_data') return 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'
  if (cls === 'master_data') return 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400'
  return 'bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400'
}

function formatCommissionValue(outcome: any): string {
  if (!outcome) return '—'
  if (outcome.percentage_value !== null && outcome.percentage_value !== undefined) return `${outcome.percentage_value}%`
  if (outcome.flat_value !== null && outcome.flat_value !== undefined) return `${outcome.flat_currency || 'INR'} ${outcome.flat_value}`
  if (outcome.tiers?.length) return `Tiered (${outcome.tiers.length} tiers)`
  return '—'
}

function formatConditions(conditions: any): string {
  if (!conditions) return 'All'
  const labelMap: Record<string, string> = {
    channel: 'channel',
    vehicle_type: 'vehicle',
    coverage_type: 'coverage',
    state: 'state',
    customer_type: 'customer',
  }
  const parts: string[] = []
  for (const [key, value] of Object.entries(conditions)) {
    if (!value) continue
    const label = labelMap[key] || key.replace(/_/g, ' ')
    if (Array.isArray(value) && value.length) {
      parts.push(`${label}: ${value.join(', ')}`)
    } else if (typeof value === 'string' && value) {
      parts.push(`${label}: ${value}`)
    } else if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
      // nested object — render key=val pairs
      const sub = Object.entries(value as Record<string, unknown>)
        .filter(([, v]) => v !== null && v !== undefined)
        .map(([k, v]) => `${k}=${v}`)
        .join(', ')
      if (sub) parts.push(`${label}: ${sub}`)
    }
  }
  return parts.join(' · ') || 'All'
}

async function copyJson(data: unknown) {
  await navigator.clipboard.writeText(JSON.stringify(data, null, 2))
  copied.value = true
  setTimeout(() => { copied.value = false }, 2000)
}

function downloadJson() {
  if (!doc.value?.cics_result) return
  const blob = new Blob([JSON.stringify(doc.value.cics_result.cics_doc, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${doc.value.original_filename.replace(/\.[^.]+$/, '')}_cics.json`
  a.click()
  URL.revokeObjectURL(url)
}

// ── Data fetching + polling ────────────────────────────────────────────────

async function getAuthHeaders() {
  const token = await userStore.getToken()
  return { Authorization: `Bearer ${token}` }
}

async function triggerIndex() {
  actionLoading.value = 'index'
  actionError.value = null
  try {
    const headers = await getAuthHeaders()
    doc.value = await $fetch<CommissionDocument>(
      `${getBaseUrl()}/api/commission-intake/documents/${route.params.id}/index/`,
      { method: 'POST', headers }
    )
  } catch (err: any) {
    actionError.value = err?.data?.detail || 'Failed to trigger indexing'
  } finally {
    actionLoading.value = null
  }
}

async function triggerExtract() {
  actionLoading.value = 'extract'
  actionError.value = null
  try {
    const headers = await getAuthHeaders()
    doc.value = await $fetch<CommissionDocument>(
      `${getBaseUrl()}/api/commission-intake/documents/${route.params.id}/extract/`,
      { method: 'POST', headers }
    )
  } catch (err: any) {
    actionError.value = err?.data?.detail || 'Failed to trigger extraction'
  } finally {
    actionLoading.value = null
  }
}

async function loadDoc() {
  try {
    const headers = await getAuthHeaders()
    doc.value = await $fetch<CommissionDocument>(
      `${getBaseUrl()}/api/commission-intake/documents/${route.params.id}/`,
      { headers }
    )
  } catch (err) {
    console.error('Failed to load document:', err)
    loadError.value = true
  }
}

async function pollDoc() {
  if (!doc.value || !isInFlight(doc.value.status)) return
  try {
    const headers = await getAuthHeaders()
    doc.value = await $fetch<CommissionDocument>(
      `${getBaseUrl()}/api/commission-intake/documents/${route.params.id}/`,
      { headers }
    )
  } catch (err) {
    console.error('Polling error:', err)
  }
}

onMounted(() => {
  loadDoc()
  pollTimer = setInterval(pollDoc, 3000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>
