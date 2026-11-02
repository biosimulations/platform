import type { VerifyWorkflowOutput } from '../models/verification'

export function verificationIsDone(output: VerifyWorkflowOutput | null): boolean {
  return !!output && ['COMPLETED', 'FAILED', 'RUN_ID_NOT_FOUND'].includes(output.workflow_status)
    && output.persistence_state !== 'pending' && output.persistence_state !== 'blocked'
}

export function verificationDisplayStatus(output: VerifyWorkflowOutput | null, halted: boolean): string {
  if (halted) return 'UNAVAILABLE'
  return output?.workflow_status || 'PENDING'
}

export function isUnavailableVerification(error: unknown): boolean {
  const e = error as { data?: { detail?: unknown }, response?: { status?: number } } | null
  return e?.response?.status === 404 && typeof e.data?.detail === 'string'
    && e.data.detail.includes('results are no longer retained')
}

// Where recovery actions may appear:
// - automatic: the original report is unavailable, so the 404 offers them unprompted
// - on_request: a finished COMPLETED or FAILED report, behind a collapsed "Recompute…"
// - none: still running or saving, or nothing to recompute
export type RecoveryMode = 'automatic' | 'on_request' | 'none'

export function recoveryMode(output: VerifyWorkflowOutput | null, unavailable: boolean): RecoveryMode {
  if (unavailable) return 'automatic'
  if (output && verificationIsDone(output)
    && (output.workflow_status === 'COMPLETED' || output.workflow_status === 'FAILED')) return 'on_request'
  return 'none'
}
