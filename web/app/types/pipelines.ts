// Pipeline domain types — mirrors backend/pipelines serializers.

export type StepType = 'PDF_TO_MD' | 'LLM_EXTRACT'

export type RunStatus = 'initiated' | 'processing' | 'completed' | 'failed'

export type StepRunStatus =
  | 'pending'
  | 'processing'
  | 'completed'
  | 'failed'
  | 'skipped_cached'

export type ParseStatus = 'ok' | 'recovered' | 'degraded'

export interface Block {
  block_id: string
  type: 'text' | 'table' | 'image' | 'equation' | 'interline_equation' | string
  text: string
  html?: string | null
  latex?: string | null
  page_idx: number
  bbox?: [number, number, number, number] | null
  /** Present when block was synthesized by splitting a parent table by <tr>. */
  row_idx?: number | null
}

export interface PipelineStep {
  id: string
  order: number
  step_type: StepType
  name: string
  config: Record<string, unknown>
}

export interface Pipeline {
  id: string
  slug: string
  name: string
  description: string
  is_seeded: boolean
  steps: PipelineStep[]
  created_at: string
}

export interface StepRun {
  id: string
  order: number
  step_type: StepType
  status: StepRunStatus
  config_snapshot: Record<string, unknown>
  output: Record<string, unknown> | null
  started_at: string | null
  finished_at: string | null
  error: string | null
}

export interface PipelineRunResult {
  extracted_fields: Record<string, string>
  citations: Record<string, string[]>
  parse_status: ParseStatus | null
  model: string | null
  input_tokens: number | null
  output_tokens: number | null
  cost_usd: number | null
}

export interface PipelineRun {
  id: string
  pipeline_run_id: string
  pipeline_slug: string
  status: RunStatus
  original_filename: string
  inputs: Record<string, unknown> | null
  result: PipelineRunResult | null
  error: string | null
  step_runs: StepRun[]
  created_at: string
  updated_at: string
}
