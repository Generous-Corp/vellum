/**
 * Recursively copy a JSON value with object keys sorted lexicographically.
 * Arrays retain source order because tree order and gradient stop order are
 * semantic. Undefined object properties are omitted, matching JSON.stringify.
 */
export function canonicalize(value) {
    if (value === null || typeof value !== 'object') return value;
    if (Array.isArray(value)) return value.map(canonicalize);

    return Object.fromEntries(
        Object.keys(value)
            .sort()
            .filter((key) => value[key] !== undefined)
            .map((key) => [key, canonicalize(value[key])]),
    );
}

/** Stable, review-friendly JSON used by generated files and CLI output. */
export function stableStringify(value, options = {}) {
    const { space = 2, newline = true } = options;
    const text = JSON.stringify(canonicalize(value), null, space);
    return newline ? `${text}\n` : text;
}

/** A compact deterministic fingerprint. This is an identity checksum, not crypto. */
export function fnv1a32(value) {
    const text = typeof value === 'string'
        ? value
        : stableStringify(value, { space: 0, newline: false });
    let hash = 0x811c9dc5;
    for (let index = 0; index < text.length; index += 1) {
        hash ^= text.charCodeAt(index);
        hash = Math.imul(hash, 0x01000193);
    }
    return (hash >>> 0).toString(36);
}

export function jsonEqual(left, right) {
    return stableStringify(left, { space: 0, newline: false }) ===
        stableStringify(right, { space: 0, newline: false });
}

export function deepClone(value) {
    if (typeof structuredClone === 'function') return structuredClone(value);
    return JSON.parse(JSON.stringify(value));
}
