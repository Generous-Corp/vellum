const SHA256_HEX = /^[0-9a-f]{64}$/i;
const SHA256_PREFIXED = /^sha256:([0-9a-f]{64})$/i;
const MAX_DATA_URL_BYTES = 8 * 1024 * 1024;
const MIME_EXTENSIONS = new Map([
    ['image/png', 'png'],
    ['image/jpeg', 'jpg'],
    ['image/gif', 'gif'],
    ['image/svg+xml', 'svg'],
    ['image/webp', 'webp'],
    ['font/ttf', 'ttf'],
    ['font/otf', 'otf'],
]);

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

function decodeBase64(value) {
    if (!/^[A-Za-z0-9+/]*={0,2}$/.test(value) || value.length % 4 !== 0) {
        throw new TypeError('asset data URL has malformed base64');
    }
    if (typeof atob !== 'function') throw new TypeError('asset data URL decoding requires atob');
    const decoded = atob(value);
    const bytes = new Uint8Array(decoded.length);
    for (let index = 0; index < decoded.length; index += 1) bytes[index] = decoded.charCodeAt(index);
    return bytes;
}

/** Decode a bounded data URL without retaining an external or absolute path. */
export function decodeAssetDataUrl(value, maximumBytes = MAX_DATA_URL_BYTES) {
    if (typeof value !== 'string' || value.length === 0 || value.length > maximumBytes * 2 + 4096) {
        throw new TypeError('asset data URL is missing or oversized');
    }
    const match = /^data:([^;,]{1,127})(;base64)?,(.*)$/s.exec(value);
    if (!match || !/^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*\/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$/.test(match[1])) {
        throw new TypeError('asset data URL has an invalid MIME type');
    }
    let bytes;
    if (match[2]) bytes = decodeBase64(match[3]);
    else {
        try {
            const text = decodeURIComponent(match[3]);
            bytes = new TextEncoder().encode(text);
        } catch (error) {
            throw new TypeError('asset data URL has malformed percent encoding', { cause: error });
        }
    }
    if (bytes.byteLength > maximumBytes) throw new TypeError('asset data URL payload is oversized');
    return { mimeType: match[1].toLowerCase(), bytes };
}

/** Return a deterministic DesignIR asset record and its localized bytes. */
export async function localizeAssetDataUrl(value, options = {}) {
    const decoded = decodeAssetDataUrl(value, options.maximumBytes ?? MAX_DATA_URL_BYTES);
    if (!globalThis.crypto?.subtle) throw new TypeError('asset localization requires Web Crypto SHA-256');
    const hash = new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256', decoded.bytes));
    const digest = normalizeSha256ContentHash(
        Array.from(hash).map((byte) => byte.toString(16).padStart(2, '0')).join(''),
        'asset.contentHash',
    );
    const extension = MIME_EXTENSIONS.get(decoded.mimeType) ?? 'bin';
    return {
        asset: {
            id: `data-${digest.slice(7, 19)}`,
            contentHash: digest,
            uri: `assets/${digest.slice(7)}.${extension}`,
            mimeType: decoded.mimeType,
            byteLength: decoded.bytes.byteLength,
        },
        bytes: decoded.bytes,
    };
}
