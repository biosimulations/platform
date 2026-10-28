/**
 * Request rules for the same-origin proxy to the legacy BioSimulators API
 * (server/routes/biosimulators-api/[...path].get.ts). No imports, so
 * tests/legacy-simulators-proxy.test.mjs can load it directly.
 */

const ONTOLOGY_NAMESPACES = new Set(['EDAM', 'SBO', 'KISAO', 'SPDX'])

export interface LegacySimulatorsTarget {
  /** Upstream path and query, e.g. `/simulators/tellurium/latest?includeTests=true` */
  upstreamPath: string
  /** Fixed key set (registry list, ontology dumps), so safe for the unbounded in-memory cache */
  cacheable: boolean
}

/**
 * Map the proxied remainder (still URL-encoded, no leading slash) onto one of
 * the upstream requests the simulators pages make, or null for anything else.
 */
export function resolveLegacySimulatorsTarget(rawPath: string, includeTests: boolean): LegacySimulatorsTarget | null {
  const segments = rawPath.split('/')
  // The URL parser resolves %2e%2e like '..', so dot segments are rejected after decoding
  for (const segment of segments) {
    let decoded: string
    try {
      decoded = decodeURIComponent(segment)
    } catch {
      return null
    }
    if (!decoded || decoded === '.' || decoded === '..' || /[/\\]/.test(decoded)) return null
  }

  const [resource, first, second, ...rest] = segments
  if (rest.length > 0) return null
  if (resource === 'ontologies' && first && !second) {
    return ONTOLOGY_NAMESPACES.has(first) ? { upstreamPath: `/ontologies/${first}`, cacheable: true } : null
  }
  if (resource !== 'simulators' || !first) return null
  if (!second) {
    return first === 'latest'
      ? { upstreamPath: '/simulators/latest', cacheable: true }
      : { upstreamPath: `/simulators/${first}`, cacheable: false }
  }
  const query = includeTests ? '?includeTests=true' : ''
  return { upstreamPath: `/simulators/${first}/${second}${query}`, cacheable: false }
}
