import { toRaw } from 'vue';

/**
 * Sanitizes an SVG path string to fix common formatting defects that
 * cause Vega's strict regex path parser to throw errors such as:
 * 'Invalid SVG path, incorrect parameter type' or 'incorrect parameter count'.
 */
export function sanitizeSvgPath(val: string): string {
  if (typeof val !== 'string' || !/^\s*[Mm]/.test(val)) {
    return val;
  }

  let fixed = val;

  // Replace literal string tokens that break regex parameter parsing
  fixed = fixed.replace(/\bNaN\b/g, '0');
  fixed = fixed.replace(/\bundefined\b/g, '0');
  fixed = fixed.replace(/\bnull\b/g, '0');

  // Collapse multiple consecutive commas
  fixed = fixed.replace(/,{2,}/g, ',');

  // Fix dangling comma immediately preceding z/Z (closepath command)
  fixed = fixed.replace(/,\s*([zZ])/g, ' $1');

  // Fix missing coordinate before next command letter
  // e.g. 'M -0.75, a' -> 'M -0.75, 0 a'
  fixed = fixed.replace(
    /([+-]?(?:(?:\d*\.\d+)|\d+))\s*,\s*([a-zA-Z])/g,
    (_match, p1, p2) => `${p1}, 0 ${p2}`
  );

  // Strip trailing commas at the end of the path
  fixed = fixed.replace(/,\s*$/g, '');

  return fixed;
}

/**
 * Recursively unwraps Vue reactive proxies and sanitizes any SVG path strings
 * or custom symbols within a Vega JSON specification.
 */
export function sanitizeVegaSpec<T = any>(spec: T): T {
  if (!spec) return spec;
  const raw = toRaw(spec);

  function walk(node: any): any {
    if (node == null) return node;

    if (typeof node === 'string') {
      return sanitizeSvgPath(node);
    }

    if (Array.isArray(node)) {
      return node.map(walk);
    }

    if (typeof node === 'object') {
      const copy: Record<string, any> = {};
      for (const [k, v] of Object.entries(node)) {
        copy[k] = walk(v);
      }
      return copy;
    }

    return node;
  }

  return walk(raw);
}
