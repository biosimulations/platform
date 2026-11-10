import type { DerivedVerificationLink, ReverifyRequest, VerificationRecovery } from '../models/verification'

// Mirrors ReverifyRequest: 1 to 10 distinct simulators and finite, non-negative tolerances
export const MAX_REVERIFY_SIMULATORS = 10

const FALLBACK_SIMULATORS = ['amici', 'copasi', 'pysces', 'tellurium', 'vcell']

export interface ReverifyDraft {
  simulators: string[]
  include_outputs: boolean
  user_description: string
  // Kept as typed, so a half-entered value stays editable; parsed when the request is built
  rel_tol: string
  abs_tol_min: string
  abs_tol_scale: string
  cache_buster: string
}

export function reverifyDraftFromOffer(recovery: VerificationRecovery | null): ReverifyDraft {
  const offer = recovery?.reverify_archive
  const defaults = offer?.defaults
  return {
    simulators: [...(offer?.default_simulators ?? FALLBACK_SIMULATORS)],
    include_outputs: defaults?.include_outputs ?? false,
    user_description: defaults?.user_description ?? 'reverification',
    rel_tol: String(defaults?.rel_tol ?? 0.0001),
    abs_tol_min: String(defaults?.abs_tol_min ?? 0.001),
    abs_tol_scale: String(defaults?.abs_tol_scale ?? 0.00001),
    cache_buster: offer?.default_cache_buster ?? '0'
  }
}

const TOLERANCES = [
  ['rel_tol', 'Relative tolerance'],
  ['abs_tol_min', 'Minimum absolute tolerance'],
  ['abs_tol_scale', 'Absolute tolerance scale']
] as const

function tolerance(value: string | number): number {
  return String(value).trim() === '' ? Number.NaN : Number(value)
}

// Problems that would make the server refuse the request; empty when the draft can be sent
export function reverifyErrors(draft: ReverifyDraft): string[] {
  const errors: string[] = []
  const simulators = draft.simulators.map(item => item.trim()).filter(Boolean)
  if (simulators.length === 0) errors.push('Choose at least one simulator.')
  if (simulators.length > MAX_REVERIFY_SIMULATORS) errors.push(`Choose at most ${MAX_REVERIFY_SIMULATORS} simulators.`)
  if (new Set(simulators).size !== simulators.length) errors.push('Each simulator can be chosen once.')
  for (const [field, label] of TOLERANCES) {
    const value = tolerance(draft[field])
    if (!Number.isFinite(value) || value < 0) errors.push(`${label} must be a number of at least 0.`)
  }
  return errors
}

export function reverifyBody(draft: ReverifyDraft): ReverifyRequest {
  return {
    simulators: draft.simulators.map(item => item.trim()).filter(Boolean),
    include_outputs: draft.include_outputs,
    user_description: draft.user_description,
    rel_tol: tolerance(draft.rel_tol),
    abs_tol_min: tolerance(draft.abs_tol_min),
    abs_tol_scale: tolerance(draft.abs_tol_scale),
    cache_buster: draft.cache_buster
  }
}

export interface DerivedGroup {
  key: 'owner_admin' | 'yours' | 'others'
  label: string
  items: DerivedVerificationLink[]
}

// Groups in the order the server lists them; "more" counts the links it left out
export function groupDerivedLinks(links: DerivedVerificationLink[], total: number): { groups: DerivedGroup[], more: number } {
  const groups: DerivedGroup[] = [
    { key: 'owner_admin', label: 'Started by the owner or an admin', items: [] },
    { key: 'yours', label: 'Started by you', items: [] },
    { key: 'others', label: 'Started by others', items: [] }
  ]
  const [ownerAdmin, yours, others] = groups as [DerivedGroup, DerivedGroup, DerivedGroup]
  for (const link of links) {
    if (link.initiator_relation === 'owner' || link.initiator_relation === 'admin') ownerAdmin.items.push(link)
    else if (link.is_yours) yours.items.push(link)
    else others.items.push(link)
  }
  return { groups: groups.filter(group => group.items.length > 0), more: Math.max(0, total - links.length) }
}
