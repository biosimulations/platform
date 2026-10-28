/**
 * Same-origin proxy to the legacy BioSimulators API. The browser can't call
 * api.biosimulators.org from our hosts: its CORS allowlist
 * (biosimulations/biosimulations apps/simulators-api/src/main.ts) doesn't list
 * them. GET-only, limited to the paths the simulators pages request, and no
 * request headers (cookies, Authorization) are forwarded.
 */

function fetchUpstream(upstreamPath: string) {
  const base = useRuntimeConfig().legacySimulatorsApiUrl.replace(/\/+$/, '')
  return fetchUpstreamJson(`${base}${upstreamPath}`)
}

// Registry list and ontology dumps only (5 keys, ~2 MB per pod): Nitro's
// production cache is in-memory with no eviction. Only 200s are stored; after
// maxAge the stale copy is served while one background refresh runs, and keeps
// being served if upstream is down.
const fetchUpstreamCached = defineCachedFunction(fetchUpstream, {
  name: 'legacy-simulators',
  maxAge: 60 * 10,
  swr: true,
  getKey: (upstreamPath: string) => upstreamPath,
  validate: entry => entry.value?.status === 200
})

export default defineEventHandler(async (event) => {
  const target = resolveLegacySimulatorsTarget(
    getRouterParam(event, 'path') ?? '',
    getQuery(event).includeTests === 'true'
  )
  if (!target) {
    throw createError({ statusCode: 404, statusMessage: 'Not Found' })
  }

  return sendUpstream(event, await (target.cacheable ? fetchUpstreamCached : fetchUpstream)(target.upstreamPath))
})
