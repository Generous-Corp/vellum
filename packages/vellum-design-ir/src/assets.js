const SHA256_HEX = /^[0-9a-f]{64}$/i;
const SHA256_PREFIXED = /^sha256:([0-9a-f]{64})$/i;

/**
 * Canonicalize either the Pulp emitter's bare SHA-256 wire form or Vellum's
 * prefixed form. Anything else is ambiguous and must not be treated as an
 * integrity claim.
 */
export function normalizeSha256ContentHash(value, path = 'asset.contentHash') {
    if (typeof value !== 'string') {
        throw new TypeError(`${path} must be a SHA-256 string`);
    }
    const trimmed = value.trim();
    const prefixed = SHA256_PREFIXED.exec(trimmed);
    if (prefixed) return `sha256:${prefixed[1].toLowerCase()}`;
    if (SHA256_HEX.test(trimmed)) return `sha256:${trimmed.toLowerCase()}`;
    throw new TypeError(`${path} must be 64 hexadecimal SHA-256 digits, optionally prefixed by 'sha256:'`);
}
