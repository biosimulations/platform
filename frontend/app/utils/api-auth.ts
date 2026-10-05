/** Match the effective ofetch destination before requesting or attaching an API token. */
export function isPlatformApiRequest(
  request: string | Request,
  baseURL: string | undefined,
  apiURL: string | undefined,
  pageOrigin: string
): boolean {
  if (!apiURL) return false
  try {
    const api = new URL(apiURL, pageOrigin)
    let target = typeof request === 'string' ? request : request.url
    // ofetch joins relative requests to baseURL, including a leading slash.
    if (typeof request === 'string' && baseURL && !/^(?:[a-z][a-z\d+.-]*:)?\/\//i.test(target)) {
      target = `${baseURL.replace(/\/$/, '')}/${target.replace(/^\//, '')}`
    }
    const destination = new URL(target, pageOrigin)
    const path = api.pathname.replace(/\/$/, '')
    return ['http:', 'https:'].includes(api.protocol)
      && !destination.username && !destination.password
      && destination.origin === api.origin
      && (destination.pathname === path || destination.pathname.startsWith(`${path}/`))
  } catch {
    return false
  }
}
