import type { H3Event } from 'h3'

/**
 * Shared by the same-origin proxies to legacy BioSimulations services
 * (server/routes/biosimulators-api, biosimulations-api, simdata-api), which the
 * browser can't call from our hosts because of their CORS allowlists.
 */

export interface UpstreamResponse {
  status: number
  contentType: string
  body: string
}

const UPSTREAM_TIMEOUT_MS = 15_000

/**
 * GET a JSON resource. Sends only `accept`, never the caller's cookies or
 * Authorization. Upstream 4xx/5xx are returned for relaying; transport
 * failures and timeouts become 502.
 */
export async function fetchUpstreamJson(url: string): Promise<UpstreamResponse> {
  try {
    const response = await $fetch.raw(url, {
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

/** Relay status, content type and body; nothing else from upstream reaches the browser */
export function sendUpstream(event: H3Event, upstream: UpstreamResponse): string {
  setResponseStatus(event, upstream.status)
  setResponseHeader(event, 'content-type', upstream.contentType)
  return upstream.body
}
