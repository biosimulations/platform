/**
 * Same-origin proxy to the legacy BioSimulators API. The browser can't call
 * api.biosimulators.org from our hosts: its CORS allowlist
 * (biosimulations/biosimulations apps/simulators-api/src/main.ts) doesn't list
 * them. GET-only, limited to the paths the simulators pages request, and no
 * request headers (cookies, Authorization) are forwarded.
 */

interface UpstreamResponse {
  status: number
  contentType: string
  body: string
}

const UPSTREAM_TIMEOUT_MS = 15_000

async function fetchUpstream(upstreamPath: string): Promise<UpstreamResponse> {
  const base = useRuntimeConfig().legacySimulatorsApiUrl.replace(/\/+$/, '')
  try {
    const response = await $fetch.raw(`${base}${upstreamPath}`, {
      headers: { accept: 'application/json' },
      responseType: 'text',
      timeout: UPSTREAM_TIMEOUT_MS,
      ignoreResponseError: true
    })
    return {
      status: response.status,
      contentType: response.headers.get('content-type') || 'application/json',
      body: String(response._data ?? '')
    }
  } catch (cause) {
    throw createError({ statusCode: 502, statusMessage: 'Bad Gateway', cause })
  }
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

  const upstream = await (target.cacheable ? fetchUpstreamCached : fetchUpstream)(target.upstreamPath)
  setResponseStatus(event, upstream.status)
  setResponseHeader(event, 'content-type', upstream.contentType)
  return upstream.body
})
