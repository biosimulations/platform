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
