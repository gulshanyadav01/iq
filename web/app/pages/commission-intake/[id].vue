<template>
  <div>
  <div class="p-8">
    <div class="max-w-7xl mx-auto space-y-6">
      <!-- Back + Header -->
      <div class="flex items-center gap-4">
        <button
          class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500 transition"
          @click="navigateTo('/commission-intake')"
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
            <span v-if="doc?.is_validated" class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400">
              <UIcon name="i-lucide-shield-check" class="w-3 h-3" />
              Validated
            </span>
          </div>
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

      <!-- Action toolbar -->
      <div v-if="doc" class="glass rounded-xl px-4 py-2.5 flex items-center justify-between gap-3 flex-wrap">
        <!-- Primary actions (left) -->
        <div class="flex items-center gap-2 flex-wrap">
          <template v-if="!isInFlight(doc.status)">
            <button
              v-if="doc.cics_result"
              class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-primary-600 hover:bg-primary-700 text-white transition shadow-sm"
              @click="downloadExcel"
            >
              <UIcon name="i-lucide-table" class="w-3.5 h-3.5" />
              Export Excel
            </button>
            <button
              v-if="doc.status === 'completed' && !doc.is_validated"
              :disabled="actionLoading !== null"
              class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-green-600 hover:bg-green-700 text-white transition shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
              @click="triggerMarkValidated"
            >
              <UIcon
                name="i-lucide-shield-check"
                class="w-3.5 h-3.5"
                :class="{ 'animate-spin': actionLoading === 'mark-validated' }"
              />
              Mark Validated
            </button>
            <button
              v-if="doc.is_validated"
              :disabled="actionLoading !== null"
              class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500 dark:text-gray-400 transition disabled:opacity-50 disabled:cursor-not-allowed"
              @click="triggerMarkInvalidated"
            >
              <UIcon
                name="i-lucide-shield-off"
                class="w-3.5 h-3.5"
                :class="{ 'animate-spin': actionLoading === 'mark-invalidated' }"
              />
              Remove Validation
            </button>
          </template>
          <span v-else class="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
            <UIcon name="i-lucide-loader-circle" class="w-3.5 h-3.5 animate-spin" />
            Pipeline running…
          </span>
        </div>

        <!-- Secondary actions (right) -->
        <div class="flex items-center gap-1">
          <button
            title="Download original document"
            class="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 transition"
            @click="downloadOriginal"
          >
            <UIcon name="i-lucide-download" class="w-3.5 h-3.5" />
            Original
          </button>
          <span class="w-px h-5 bg-gray-200 dark:bg-gray-700 mx-1" />
          <template v-if="!isInFlight(doc.status)">
            <button
              :disabled="actionLoading !== null"
              title="Re-run indexing from source document"
              class="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 transition disabled:opacity-50 disabled:cursor-not-allowed"
              @click="triggerIndex"
            >
              <UIcon
                name="i-lucide-scan-text"
                class="w-3.5 h-3.5"
                :class="{ 'animate-spin': actionLoading === 'index' }"
              />
              Re-index
            </button>
            <!-- Split button: Extract serves the cached result; the menu forces a fresh LLM run. -->
            <div v-if="doc.indexed_content" class="flex items-center ml-1">
              <button
                :disabled="actionLoading !== null"
                title="Re-run LLM extraction on indexed content (serves the cached result if the input is unchanged)"
                class="flex items-center gap-1.5 px-2.5 py-1.5 rounded-l-lg text-xs font-medium border border-primary-200 dark:border-primary-800 text-primary-700 dark:text-primary-300 bg-primary-50/50 dark:bg-primary-900/20 hover:bg-primary-100 dark:hover:bg-primary-900/40 transition disabled:opacity-50 disabled:cursor-not-allowed"
                @click="triggerExtract()"
              >
                <UIcon
                  name="i-lucide-sparkles"
                  class="w-3.5 h-3.5"
                  :class="{ 'animate-spin': actionLoading === 'extract' }"
                />
                Extract
              </button>
              <UDropdownMenu :items="extractMenuItems" :content="{ align: 'end' }">
                <button
                  :disabled="actionLoading !== null"
                  title="More extraction options"
                  aria-label="More extraction options"
                  class="flex items-center px-1.5 py-1.5 rounded-r-lg border border-l-0 border-primary-200 dark:border-primary-800 text-primary-700 dark:text-primary-300 bg-primary-50/50 dark:bg-primary-900/20 hover:bg-primary-100 dark:hover:bg-primary-900/40 transition disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <UIcon name="i-lucide-chevron-down" class="w-3.5 h-3.5" />
                </button>
              </UDropdownMenu>
            </div>
          </template>
          <button
            :disabled="refreshing"
            title="Refresh status"
            class="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 transition disabled:opacity-50 disabled:cursor-not-allowed"
            @click="refresh"
          >
            <UIcon
              name="i-lucide-refresh-cw"
              class="w-3.5 h-3.5"
              :class="{ 'animate-spin': refreshing }"
            />
            Refresh
          </button>
        </div>
      </div>

      <!-- Action error -->
      <div v-if="actionError" class="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-400 px-4 py-2 rounded-lg text-xs">
        {{ actionError }}
      </div>

      <!--
        Sheet coverage notice. Document-level, not stage-level: coverage is a
        property of the document, and nothing routes a reader to the Indexed
        pane (the stage watcher lands on Validation for a completed document).

        Deliberately neutral, not amber. A hidden tab is almost always hidden on
        purpose, so this is a statement of what was read, not a problem to fix.
        Colouring it like a warning trains people to ignore it.
      -->
      <div
        v-if="showCoverageStrip && sheetCoverage"
        class="flex items-start gap-2.5 bg-gray-50 dark:bg-gray-800/40 border border-gray-200 dark:border-gray-700 rounded-xl px-4 py-2.5 text-xs text-gray-600 dark:text-gray-400"
      >
        <UIcon name="i-lucide-eye-off" class="w-3.5 h-3.5 mt-0.5 flex-shrink-0 text-gray-400" />
        <div class="min-w-0 flex-1">
          <span class="font-medium text-gray-700 dark:text-gray-300">{{ coverageStripText }}</span>
          <!-- First few names only. Listing all 10 turned the strip into the
               wall of text the redesign removed; "View sheets" has the rest. -->
          <span v-if="sheetCoverage.hiddenNames.length" class="ml-1 break-words">
            Hidden: {{ sheetCoverage.hiddenNames.slice(0, 3).join(', ')
            }}<span v-if="sheetCoverage.hiddenNames.length > 3"> +{{ sheetCoverage.hiddenNames.length - 3 }} more</span>
          </span>
        </div>
        <button
          class="flex items-center gap-1 px-2 py-1 rounded text-xs font-medium text-gray-600 dark:text-gray-300 hover:bg-gray-200/60 dark:hover:bg-white/[0.06] hover:text-gray-900 dark:hover:text-white transition flex-shrink-0"
          @click="viewHiddenSheets"
        >
          <UIcon name="i-lucide-eye" class="w-3 h-3" />
          View sheets
        </button>
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
              <!-- Excel: sheet list — extracted sheets only -->
              <template v-if="doc.indexed_content.source_type === 'excel'">
                <div v-for="sheetName in excelSheets.visible" :key="sheetName" class="space-y-2">
                  <div class="flex items-center gap-2">
                    <span class="text-sm font-medium text-gray-900 dark:text-white truncate max-w-md" :title="sheetName">{{ sheetName }}</span>
                    <span :class="['px-1.5 py-0.5 rounded text-[10px] font-medium uppercase', getSheetClassBadge(sheetName, doc.indexed_content)]">
                      {{ getSheetClassification(sheetName, doc.indexed_content) }}
                    </span>
                  </div>
                  <details class="group">
                    <summary class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 cursor-pointer select-none">
                      Show content
                    </summary>
                    <div class="mt-2 overflow-x-auto max-h-64 overflow-y-auto rounded-lg bg-gray-50 dark:bg-gray-900 p-4">
                      <!-- eslint-disable-next-line vue/no-v-html -->
                      <div class="prose prose-xs dark:prose-invert max-w-none text-xs whitespace-nowrap [&_table]:border-collapse [&_td]:border [&_td]:border-gray-200 [&_td]:dark:border-gray-700 [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:border-gray-200 [&_th]:dark:border-gray-700 [&_th]:px-2 [&_th]:py-1 [&_th]:bg-gray-100 [&_th]:dark:bg-gray-800" v-html="renderMd(doc.indexed_content.sheet_contents[sheetName] as string)" />
                    </div>
                  </details>
                </div>
                <div ref="hiddenSheetsAnchor">
                  <HiddenSheetsSection
                    :names="excelSheets.hidden"
                    :indexed="doc.indexed_content"
                    :open-request="hiddenSheetsOpenRequest"
                  />
                </div>
              </template>
              <!-- Email -->
              <template v-else-if="doc.indexed_content.source_type === 'email'">
                <div v-if="doc.indexed_content.email_context" class="space-y-1">
                  <details class="group">
                    <summary class="text-xs font-semibold text-gray-500 uppercase tracking-widest cursor-pointer select-none hover:text-gray-700 dark:hover:text-gray-300">Email Headers</summary>
                    <pre class="mt-2 text-xs bg-gray-50 dark:bg-gray-900 p-4 rounded-lg whitespace-pre-wrap">{{ doc.indexed_content.email_context }}</pre>
                  </details>
                </div>
                <div class="space-y-1">
                  <p class="text-xs font-semibold text-gray-500 uppercase tracking-widest">Body</p>
                  <details class="group" open>
                    <summary class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 cursor-pointer select-none">Show content</summary>
                    <pre class="mt-2 text-xs bg-gray-50 dark:bg-gray-900 p-4 rounded-lg overflow-auto max-h-96 leading-relaxed whitespace-pre-wrap">{{ doc.indexed_content.primary_content }}</pre>
                  </details>
                </div>
                <!-- Excel attachments extracted from email -->
                <template v-if="doc.indexed_content.sheet_contents && Object.keys(doc.indexed_content.sheet_contents).length">
                  <div class="pt-2 border-t border-gray-100 dark:border-gray-800 space-y-2">
                    <p class="text-xs font-semibold text-gray-500 uppercase tracking-widest mb-3">Excel Attachments</p>
                    <div v-for="sheetName in excelSheets.visible" :key="sheetName">
                      <div class="flex items-center gap-2">
                        <span class="text-sm font-medium text-gray-900 dark:text-white truncate max-w-md" :title="sheetName">{{ sheetName }}</span>
                        <span :class="['px-1.5 py-0.5 rounded text-[10px] font-medium uppercase', getSheetClassBadge(sheetName, doc.indexed_content)]">
                          {{ getSheetClassification(sheetName, doc.indexed_content) }}
                        </span>
                      </div>
                      <details class="group">
                        <summary class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 cursor-pointer select-none">
                          Show content
                        </summary>
                        <div class="mt-2 overflow-x-auto max-h-64 overflow-y-auto rounded-lg bg-gray-50 dark:bg-gray-900 p-4">
                          <!-- eslint-disable-next-line vue/no-v-html -->
                          <div class="prose prose-xs dark:prose-invert max-w-none text-xs whitespace-nowrap [&_table]:border-collapse [&_td]:border [&_td]:border-gray-200 [&_td]:dark:border-gray-700 [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:border-gray-200 [&_th]:dark:border-gray-700 [&_th]:px-2 [&_th]:py-1 [&_th]:bg-gray-100 [&_th]:dark:bg-gray-800" v-html="renderMd(doc.indexed_content.sheet_contents[sheetName] as string)" />
                        </div>
                      </details>
                    </div>
                    <div ref="hiddenSheetsAnchor">
                  <HiddenSheetsSection
                    :names="excelSheets.hidden"
                    :indexed="doc.indexed_content"
                    :open-request="hiddenSheetsOpenRequest"
                  />
                </div>
                  </div>
                </template>
              </template>
              <!-- PDF -->
              <template v-else>
                <details class="group">
                  <summary class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 cursor-pointer select-none">Show extracted text</summary>
                  <pre class="mt-2 text-xs bg-gray-50 dark:bg-gray-900 p-4 rounded-lg overflow-auto max-h-96 leading-relaxed whitespace-pre-wrap">{{ doc.indexed_content.primary_content }}</pre>
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
                  <div class="flex items-center gap-1.5 mt-0.5 flex-wrap">
                    <span class="text-xs text-gray-500">{{ currentPass.llm_model_used || doc.llm_provider }}</span>
                    <span v-if="currentPass.duration_seconds" class="text-xs text-gray-500"> · {{ currentPass.duration_seconds.toFixed(1) }}s</span>
                    <span v-if="currentPass.extraction_method === 'codegen'" class="px-1.5 py-0.5 rounded text-[10px] font-medium bg-violet-100 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300 uppercase">codegen</span>
                    <span v-if="currentPass.status && currentPass.status !== 'completed'" :class="['px-1.5 py-0.5 rounded text-[10px] font-medium uppercase', getPassStatusBadge(currentPass.status)]">{{ currentPass.status }}</span>
                  </div>
                </div>
                <div class="flex items-center gap-2">
                  <button
                    v-if="currentPass.system_instruction || currentPass.prompt_text"
                    class="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition"
                    title="View prompts"
                    @click="promptModalTab = 'system'; promptModalOpen = true"
                  >
                    <UIcon name="i-lucide-file-code" class="w-3.5 h-3.5" />
                    Prompts
                  </button>
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

              <!-- Errors / warnings / notices banner. Notices are the transformer's
                   audit trail of deliberate normalizations, so they get a neutral
                   style and never outrank a real problem. -->
              <div v-if="doc.cics_result.errors.length" class="mx-6 mt-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg px-4 py-2 text-xs text-red-700 dark:text-red-400">
                {{ doc.cics_result.errors.length }} transform error(s): {{ doc.cics_result.errors[0] }}
              </div>
              <div v-else-if="doc.cics_result.warnings.length" class="mx-6 mt-4 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded-lg px-4 py-2 text-xs text-yellow-700 dark:text-yellow-400">
                {{ doc.cics_result.warnings.length }} transform warning(s)
              </div>
              <div v-else-if="doc.cics_result.notices?.length" class="mx-6 mt-4 bg-gray-50 dark:bg-gray-900/40 border border-gray-200 dark:border-gray-800 rounded-lg px-4 py-2 text-xs text-gray-500 dark:text-gray-400">
                {{ doc.cics_result.notices.length }} transform notice(s) — normalizations applied, no action needed
              </div>

              <!-- Summary tab -->
              <div v-if="activeCicsTab === 'summary'" class="p-6 space-y-6">
                <!-- Inherited rates banner -->
                <div
                  v-if="(doc.cics_result!.cics_doc as any)?.change_reference?.is_inherited"
                  class="flex items-start gap-2.5 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-xl px-4 py-3 text-xs text-amber-800 dark:text-amber-300"
                >
                  <UIcon name="i-lucide-copy" class="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
                  <div>
                    <span class="font-semibold">Rates inherited from previous period — no new products in this document.</span>
                    <span v-if="(doc.cics_result!.cics_doc as any)?.change_reference?.inheritance_notes" class="ml-1 text-amber-700 dark:text-amber-400">{{ (doc.cics_result!.cics_doc as any).change_reference.inheritance_notes }}</span>
                  </div>
                </div>
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
                <div v-if="!cicsProducts.length" class="text-sm text-gray-500">
                  <span v-if="(doc.cics_result!.cics_doc as any)?.change_reference?.is_inherited">
                    No products extracted — rates are inherited from a previous period. See the Summary tab for details.
                  </span>
                  <span v-else>No products found.</span>
                </div>
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
                          <th class="pb-2 w-4" />
                        </tr>
                      </thead>
                      <tbody>
                        <template v-for="rule in product.commission_rules" :key="rule.rule_id">
                          <tr
                            class="align-top border-t border-gray-100 dark:border-gray-800 cursor-pointer hover:bg-white/60 dark:hover:bg-gray-700/30 transition"
                            @click="expandedRuleId = expandedRuleId === rule.rule_id ? null : rule.rule_id"
                          >
                            <td class="py-2 pr-4 font-mono text-gray-500">{{ rule.rule_id }}</td>
                            <td class="py-2 pr-4 text-gray-700 dark:text-gray-300">{{ rule.outcome?.commission_type || '—' }}</td>
                            <td class="py-2 pr-4 text-gray-900 dark:text-gray-100 font-medium">{{ formatCommissionValue(rule.outcome) }}</td>
                            <td class="py-2 pr-4 text-gray-500 dark:text-gray-400 max-w-xs truncate">{{ formatConditions(rule.conditions) }}</td>
                            <td class="py-2 text-gray-500">{{ rule.priority ?? '—' }}</td>
                            <td class="py-2 text-right">
                              <UIcon
                                :name="expandedRuleId === rule.rule_id ? 'i-lucide-chevron-up' : 'i-lucide-chevron-down'"
                                class="w-3.5 h-3.5 text-gray-400"
                              />
                            </td>
                          </tr>
                          <tr v-if="expandedRuleId === rule.rule_id" class="bg-white/70 dark:bg-gray-800/40 border-t border-gray-100 dark:border-gray-800">
                            <td colspan="6" class="px-4 py-3">
                              <p class="text-[10px] font-semibold text-gray-400 uppercase tracking-widest mb-2">Conditions</p>
                              <div v-if="rule.conditions && Object.keys(rule.conditions).length" class="grid grid-cols-2 sm:grid-cols-3 gap-x-6 gap-y-1.5">
                                <div
                                  v-for="(val, field) in rule.conditions"
                                  :key="field"
                                  class="flex items-start gap-2 text-xs"
                                >
                                  <span class="font-mono text-blue-500 dark:text-blue-400 whitespace-nowrap flex-shrink-0">{{ String(field).replace(/_/g, ' ') }}</span>
                                  <span class="text-gray-700 dark:text-gray-300 break-all">{{ Array.isArray(val) ? val.join(', ') : typeof val === 'boolean' ? (val ? 'yes' : 'no') : val }}</span>
                                </div>
                              </div>
                              <span v-else class="text-xs text-gray-400 italic">No conditions (catch-all rule)</span>
                            </td>
                          </tr>
                        </template>
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
                  <div class="border border-gray-100 dark:border-gray-800 rounded-xl overflow-hidden">
                    <table class="w-full text-xs">
                      <thead>
                        <tr class="text-left text-gray-400 border-b border-gray-100 dark:border-gray-800 bg-gray-50 dark:bg-gray-800/30">
                          <th class="py-2 pl-3 pr-4 font-semibold">Severity</th>
                          <th class="py-2 pr-4 font-semibold">Type</th>
                          <th class="py-2 pr-4 font-semibold">Product</th>
                          <th class="py-2 font-semibold">Rules</th>
                          <th class="py-2 pr-3 font-semibold" />
                        </tr>
                      </thead>
                      <tbody>
                        <template v-for="(overlap, i) in doc.validation_result.overlap_issues" :key="i">
                          <tr
                            class="align-top border-t border-gray-100 dark:border-gray-800 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/30 transition"
                            @click="expandedOverlap = expandedOverlap === i ? null : i"
                          >
                            <td class="py-2 pl-3 pr-4">
                              <span :class="['px-1.5 py-0.5 rounded text-[10px] font-medium uppercase', getSeverityBadge(overlap.severity)]">{{ overlap.severity }}</span>
                            </td>
                            <td class="py-2 pr-4 text-gray-600 dark:text-gray-400">{{ overlap.overlap_type }}</td>
                            <td class="py-2 pr-4 font-mono text-gray-500">{{ overlap.product_code }}</td>
                            <td class="py-2 text-gray-700 dark:text-gray-300 font-mono">{{ overlap.rule1_id }} vs {{ overlap.rule2_id }}</td>
                            <td class="py-2 pr-3 text-right">
                              <div class="flex items-center justify-end gap-2">
                                <button
                                  class="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium text-primary-600 dark:text-primary-400 hover:bg-primary-50 dark:hover:bg-primary-900/20 transition"
                                  @click.stop="openOverlapRuleModal(overlap)"
                                >
                                  <UIcon name="i-lucide-eye" class="w-3 h-3" />
                                  View Rules
                                </button>
                                <UIcon
                                  :name="expandedOverlap === i ? 'i-lucide-chevron-up' : 'i-lucide-chevron-down'"
                                  class="w-3.5 h-3.5 text-gray-400"
                                />
                              </div>
                            </td>
                          </tr>
                          <tr v-if="expandedOverlap === i" class="bg-gray-50 dark:bg-gray-800/20 border-t border-gray-100 dark:border-gray-800">
                            <td colspan="5" class="px-4 py-3 space-y-2">
                              <p v-if="overlap.description" class="text-gray-700 dark:text-gray-300">{{ overlap.description }}</p>
                              <div v-if="overlap.overlapping_dimensions?.length" class="flex items-center gap-1.5 flex-wrap">
                                <span class="text-gray-400 dark:text-gray-500">Dimensions:</span>
                                <span
                                  v-for="dim in overlap.overlapping_dimensions"
                                  :key="dim"
                                  class="px-1.5 py-0.5 rounded bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 font-mono"
                                >{{ dim }}</span>
                              </div>
                              <div v-if="overlap.suggestion" class="flex items-start gap-1.5 text-gray-500 dark:text-gray-400">
                                <UIcon name="i-lucide-lightbulb" class="w-3.5 h-3.5 mt-0.5 flex-shrink-0 text-amber-500" />
                                <span>{{ overlap.suggestion }}</span>
                              </div>
                            </td>
                          </tr>
                        </template>
                      </tbody>
                    </table>
                  </div>
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

  <!-- Overlap rule review modal -->
  <UModal v-model:open="overlapRuleModalOpen" title="Overlapping Rules" :ui="{ content: 'max-w-4xl' }">
    <template #body>
      <div v-if="overlapRuleModalData" class="space-y-4 overflow-y-auto max-h-[75vh]">
        <!-- Header info -->
        <div class="flex items-center gap-3 text-xs text-gray-500 dark:text-gray-400">
          <span :class="['px-1.5 py-0.5 rounded font-medium uppercase', getSeverityBadge(overlapRuleModalData.severity)]">{{ overlapRuleModalData.severity }}</span>
          <span>{{ overlapRuleModalData.overlap_type }}</span>
          <span class="font-mono text-gray-400">{{ overlapRuleModalData.product_code }}</span>
        </div>
        <p v-if="overlapRuleModalData.description" class="text-sm text-gray-700 dark:text-gray-300">{{ overlapRuleModalData.description }}</p>
        <div v-if="overlapRuleModalData.overlapping_dimensions?.length" class="flex items-center gap-1.5 flex-wrap text-xs">
          <span class="text-gray-400">Conflicting dimensions:</span>
          <span
            v-for="dim in overlapRuleModalData.overlapping_dimensions"
            :key="dim"
            class="px-1.5 py-0.5 rounded bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 font-mono"
          >{{ dim }}</span>
        </div>
        <div v-if="overlapRuleModalData.suggestion" class="flex items-start gap-1.5 text-xs text-gray-500 dark:text-gray-400 bg-amber-50 dark:bg-amber-900/10 border border-amber-100 dark:border-amber-800 rounded-lg px-3 py-2">
          <UIcon name="i-lucide-lightbulb" class="w-3.5 h-3.5 mt-0.5 flex-shrink-0 text-amber-500" />
          <span>{{ overlapRuleModalData.suggestion }}</span>
        </div>
        <!-- Side-by-side rules -->
        <div class="grid grid-cols-2 gap-4 pt-2">
          <template v-for="ruleId in [overlapRuleModalData.rule1_id, overlapRuleModalData.rule2_id]" :key="ruleId">
            <div class="bg-gray-50 dark:bg-gray-800/50 rounded-xl overflow-hidden">
              <div class="px-4 py-2 border-b border-gray-200 dark:border-gray-700">
                <span class="text-xs font-mono font-semibold text-gray-700 dark:text-gray-300">{{ ruleId }}</span>
              </div>
              <template v-if="getRuleById(ruleId) as any">
                <div class="p-4 text-xs space-y-2">
                  <div class="flex items-center gap-2">
                    <span class="text-gray-400 w-16 flex-shrink-0">Type</span>
                    <span class="text-gray-900 dark:text-gray-100">{{ (getRuleById(ruleId) as any).outcome?.commission_type || '—' }}</span>
                  </div>
                  <div class="flex items-center gap-2">
                    <span class="text-gray-400 w-16 flex-shrink-0">Value</span>
                    <span class="text-gray-900 dark:text-gray-100 font-medium">{{ formatCommissionValue((getRuleById(ruleId) as any).outcome) }}</span>
                  </div>
                  <div class="flex items-center gap-2">
                    <span class="text-gray-400 w-16 flex-shrink-0">Priority</span>
                    <span class="text-gray-900 dark:text-gray-100">{{ (getRuleById(ruleId) as any).priority ?? '—' }}</span>
                  </div>
                </div>
                <div v-if="Object.keys((getRuleById(ruleId) as any).conditions ?? {}).length" class="px-4 pb-4 border-t border-gray-200 dark:border-gray-700">
                  <p class="text-[10px] font-semibold text-gray-400 uppercase tracking-widest pt-3 pb-2">Conditions</p>
                  <table class="w-full text-xs">
                    <thead>
                      <tr class="text-left text-gray-400 border-b border-gray-100 dark:border-gray-800">
                        <th class="pb-1.5 pr-3 font-semibold">Field</th>
                        <th class="pb-1.5 font-semibold">Value</th>
                      </tr>
                    </thead>
                    <tbody class="divide-y divide-gray-100 dark:divide-gray-800">
                      <tr
                        v-for="(val, field) in (getRuleById(ruleId) as any).conditions"
                        :key="field"
                        class="align-top"
                      >
                        <td class="py-1.5 pr-3 font-mono text-blue-500 dark:text-blue-400 whitespace-nowrap">{{ field }}</td>
                        <td class="py-1.5 text-gray-700 dark:text-gray-300 break-all">{{ Array.isArray(val) ? val.join(', ') : val }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <div v-else class="px-4 pb-4 text-xs text-gray-400 italic">No conditions (catch-all rule)</div>
              </template>
              <div v-else class="p-4 text-xs text-gray-400 italic">Rule not found in CICS result</div>
            </div>
          </template>
        </div>
      </div>
    </template>
  </UModal>

  <!-- Prompt viewer modal -->
  <UModal v-model:open="promptModalOpen" title="Pass Prompts" :ui="{ content: 'max-w-3xl' }">
    <template #body>
      <div v-if="currentPass">
        <!-- Tabs -->
        <div class="flex gap-4 text-xs font-medium border-b border-gray-200 dark:border-gray-700 mb-4">
          <button
            :class="['pb-3 border-b-2 transition', promptModalTab === 'system' ? 'border-primary-500 text-primary-600 dark:text-primary-400' : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300']"
            @click="promptModalTab = 'system'"
          >
            System Instruction
          </button>
          <button
            :class="['pb-3 border-b-2 transition', promptModalTab === 'user' ? 'border-primary-500 text-primary-600 dark:text-primary-400' : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300']"
            @click="promptModalTab = 'user'"
          >
            User Prompt
          </button>
        </div>
        <div class="overflow-x-auto overflow-y-auto max-h-[60vh] rounded-lg bg-gray-50 dark:bg-gray-900 p-4">
          <!-- eslint-disable-next-line vue/no-v-html -->
          <div class="prose prose-xs dark:prose-invert max-w-none text-xs [&_table]:border-collapse [&_td]:border [&_td]:border-gray-200 [&_td]:dark:border-gray-700 [&_td]:px-2 [&_td]:py-1 [&_td]:whitespace-nowrap [&_th]:border [&_th]:border-gray-200 [&_th]:dark:border-gray-700 [&_th]:px-2 [&_th]:py-1 [&_th]:bg-gray-100 [&_th]:dark:bg-gray-800 [&_th]:whitespace-nowrap" v-html="renderMd(promptModalTab === 'system' ? (currentPass.system_instruction || '—') : (currentPass.prompt_text || '—'))" />
        </div>
      </div>
    </template>
  </UModal>
  </div>
</template>

<script setup lang="ts">
import { nextTick, ref } from 'vue'
import MarkdownIt from 'markdown-it'
import { useCommissionIntakeDetail } from '~/composables/useCommissionIntakeDetail'
import type { OverlapIssue } from '~/composables/useCommissionIntakeDetail'

const md = new MarkdownIt({ html: false, breaks: false })

function renderMd(content: string): string {
  return md.render(content)
}

definePageMeta({ layout: 'default', middleware: 'auth' })

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

const promptModalOpen = ref(false)
const promptModalTab = ref<'system' | 'user'>('system')

const expandedOverlap = ref<number | null>(null)
const expandedRuleId = ref<string | null>(null)
const overlapRuleModalOpen = ref(false)
const overlapRuleModalData = ref<OverlapIssue | null>(null)

const {
  doc, loadError, activeStage, copied, activeCicsTab, actionLoading, actionError, activePassSheetKey,
  refreshing,
  stages, currentPassSheets, currentPass,
  cicsMetrics, cicsMetadataRows, cicsProducts,
  validationBannerClass, validationBannerIcon, validationBannerText,
  sheetCoverage, showCoverageStrip, coverageStripText, excelSheets,
  isInFlight, getStatusBadgeClass, formatStatus, getPassStatusBadge, getSeverityBadge,
  getSheetClassification, getSheetClassBadge,
  formatCommissionValue, formatConditions,
  sheetPassStatus, copyJson, downloadJson, downloadExcel, downloadOriginal, refresh,
  triggerIndex, triggerExtract, triggerRerunPass, triggerTransform, triggerValidate,
  triggerMarkValidated, triggerMarkInvalidated,
} = useCommissionIntakeDetail()

// Extract split-button menu. A plain Extract click is cache-backed; this is the
// deliberate, more expensive path — it bypasses the pinned result and re-rolls
// the LLM passes, so it lives behind the caret rather than in the toolbar.
const extractMenuItems = computed(() => [
  [
    {
      label: 'Fresh extract (bypass cache)',
      icon: 'i-lucide-refresh-ccw-dot',
      disabled: actionLoading.value !== null,
      onSelect: () => triggerExtract({ forceRefresh: true }),
    },
  ],
])

// "View sheets" on the coverage notice.
//
// Switching activeStage alone did nothing: it defaults to 'indexed' and the
// stage watcher only reassigns when the current stage is unavailable, so the
// page is already on Indexed and the click was a no-op. What the reader
// actually wants is the skipped sheets, which sit below the fold — so scroll
// to them and expand the group.
const hiddenSheetsAnchor = ref<HTMLElement | null>(null)
// Counter, not a flag — see the openRequest prop on HiddenSheetsSection. A
// boolean would stop re-opening the group after the first click.
const hiddenSheetsOpenRequest = ref(0)

async function viewHiddenSheets() {
  activeStage.value = 'indexed'
  hiddenSheetsOpenRequest.value++
  await nextTick()
  hiddenSheetsAnchor.value?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

function openOverlapRuleModal(overlap: OverlapIssue) {
  overlapRuleModalData.value = overlap
  overlapRuleModalOpen.value = true
}

function getRuleById(ruleId: string) {
  for (const product of cicsProducts.value) {
    const rule = (product.commission_rules as { rule_id: string }[])?.find(r => r.rule_id === ruleId)
    if (rule) return rule
  }
  return null
}
</script>
