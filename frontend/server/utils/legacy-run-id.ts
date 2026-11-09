/**
 * Run-ID rule for the run-precheck proxies (server/routes/biosimulations-api,
 * server/routes/simdata-api). No imports, so tests/legacy-run-id.test.mjs can
 * load it directly.
 */

// BioSimulations run IDs are 24-hex Mongo ObjectIds. The rule is wider so an
// unusual ID reaches upstream and gets its own 404, but it admits no '%', '.'
// or '/', so an ID is always exactly one literal path segment.
const RUN_ID = /^[\w-]{1,64}$/

export function legacyRunIdSegment(rawId: string | undefined): string | null {
  return rawId !== undefined && RUN_ID.test(rawId) ? rawId : null
}
