import type { VerificationIdsResponse, VerificationListItem, VerificationType } from '~/models/verification'

// The validation the backend applies to omex_hash: an md5 hexdigest, like /compatibility/check's omex_id
export const OMEX_HASH_PATTERN = /^[0-9a-f]{32}$/

// The backend's default workflow_id_prefix per verification type; the GUI never overrides it
const DEFAULT_ID_PREFIXES: Record<VerificationType, string> = {
  omex: 'omex-verification-',
  runs: 'runs-verification-'
}

export interface VerificationListingPage {
  rows: VerificationListItem[]
  nextCursor: string | null
  // False for backends that predate listing summaries: rows are bare IDs and omex_hash is ignored
  summaries: boolean
}

/** Reads one page of GET /verification_ids; `omexHash` is the archive filter the request was sent with. */
export function parseVerificationListing(
  res: VerificationIdsResponse,
  omexHash: string | null = null
): VerificationListingPage {
  if (Array.isArray(res.verifications)) {
    return { rows: res.verifications, nextCursor: res.next_cursor ?? null, summaries: true }
  }
  // Older backends list bare IDs and ignore omex_hash, so no row can be tied to an archive
  if (omexHash) {
    return { rows: [], nextCursor: null, summaries: false }
  }
  return {
    rows: (res.verification_ids ?? []).map(workflow_id => ({ workflow_id })),
    nextCursor: res.next_cursor ?? null,
    summaries: false
  }
}

/**
 * A verification's type: the server's `verify_type`, else the one its default ID prefix implies
 * (older backends and unloaded rows). An ID with a custom prefix has no known type.
 */
export function verificationTypeOf(item: Pick<VerificationListItem, 'workflow_id' | 'verify_type'>): VerificationType | null {
  if (item.verify_type) return item.verify_type
  if (item.workflow_id.startsWith(DEFAULT_ID_PREFIXES.omex)) return 'omex'
  if (item.workflow_id.startsWith(DEFAULT_ID_PREFIXES.runs)) return 'runs'
  return null
}

/** Parses a server timestamp, reading offset-less values as UTC (the ledger stores UTC). */
export function parseServerTimestamp(value: string): Date {
  const hasOffset = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)
  // Python emits microseconds; not every browser parses more than millisecond precision
  const normalized = value.replace(/(\.\d{3})\d+/, '$1')
  return new Date(hasOffset ? normalized : `${normalized}Z`)
}
