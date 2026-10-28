/**
 * Same-origin proxy for one BioSimulations run record (api.biosimulatIONS.org,
 * not the simulator registry at /biosimulators-api), used by the verify-model
 * run precheck: the upstream's CORS allowlist doesn't include our hosts.
 * Not cached -- a run's status changes while it executes.
 */
export default defineEventHandler(async (event) => {
  const id = legacyRunIdSegment(getRouterParam(event, 'id'))
  if (!id) {
    throw createError({ statusCode: 404, statusMessage: 'Not Found' })
  }
  const base = useRuntimeConfig(event).legacyRunsApiUrl.replace(/\/+$/, '')
  return sendUpstream(event, await fetchUpstreamJson(`${base}/runs/${id}`))
})
