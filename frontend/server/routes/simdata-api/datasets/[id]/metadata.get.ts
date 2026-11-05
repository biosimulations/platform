/**
 * Same-origin proxy for one run's HDF5 dataset metadata, used by the
 * verify-model run precheck: simdata.api.biosimulations.org's CORS allowlist
 * doesn't include our hosts. Not cached -- the key space is every run.
 */
export default defineEventHandler(async (event) => {
  const id = legacyRunIdSegment(getRouterParam(event, 'id'))
  if (!id) {
    throw createError({ statusCode: 404, statusMessage: 'Not Found' })
  }
  const base = useRuntimeConfig(event).simdataApiUrl.replace(/\/+$/, '')
  return sendUpstream(event, await fetchUpstreamJson(`${base}/datasets/${id}/metadata`))
})
