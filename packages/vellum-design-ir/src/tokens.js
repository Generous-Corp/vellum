import { canonicalize, deepClone } from './stable-json.js';

const TOKEN_REFERENCE = /\{([^{}]+)\}/g;

export function normalizeTokens(rawTokens, sourceKey) {
    if (rawTokens === undefined || rawTokens === null) return {};
    if (typeof rawTokens !== 'object' || Array.isArray(rawTokens)) {
        throw new TypeError('tokens must be an object');
    }

    const output = {};
    flattenTokens(rawTokens, [], output, sourceKey);
    return canonicalize(output);
}

function flattenTokens(value, path, output, sourceKey) {
    for (const key of Object.keys(value).sort()) {
        if (!key || key.includes('{') || key.includes('}')) {
            throw new TypeError(`invalid token segment at ${[...path, key].join('.')}`);
        }
        const item = value[key];
        const nextPath = [...path, key];
        if (isTokenLeaf(item)) {
            const localName = nextPath.join('.');
            const name = `${sourceKey}.${localName}`;
            output[name] = {
                $type: item.$type ?? inferTokenType(item.$value),
                $value: qualifyReferences(deepClone(item.$value), sourceKey),
                sourcePath: localName,
            };
            if (item.$description !== undefined) {
                output[name].$description = String(item.$description);
            }
            continue;
        }
        if (!item || typeof item !== 'object' || Array.isArray(item)) {
            throw new TypeError(`token group ${nextPath.join('.')} must contain token objects`);
        }
        flattenTokens(item, nextPath, output, sourceKey);
    }
}

function isTokenLeaf(value) {
    return Boolean(value && typeof value === 'object' && !Array.isArray(value) && '$value' in value);
}

function inferTokenType(value) {
    if (typeof value === 'number') return 'number';
    if (typeof value === 'boolean') return 'boolean';
    if (typeof value === 'string' && /^#[0-9a-f]{3,8}$/i.test(value)) return 'color';
    if (
        value &&
        typeof value === 'object' &&
        typeof value.value === 'number' &&
        typeof value.unit === 'string'
    ) {
        return 'dimension';
    }
    return 'string';
}

function qualifyReferences(value, sourceKey) {
    if (typeof value === 'string') {
        return value.replace(TOKEN_REFERENCE, (_match, reference) => {
            const trimmed = reference.trim();
            return trimmed.startsWith(`${sourceKey}.`)
                ? `{${trimmed}}`
                : `{${sourceKey}.${trimmed}}`;
        });
    }
    if (Array.isArray(value)) return value.map((item) => qualifyReferences(item, sourceKey));
    if (value && typeof value === 'object') {
        return Object.fromEntries(
            Object.entries(value).map(([key, item]) => [key, qualifyReferences(item, sourceKey)]),
        );
    }
    return value;
}

export function resolveTokenLayers(document, authoredOverlay = {}) {
    const diagnostics = [];
    const primitive = Object.create(null);
    const resolving = new Set();

    function resolvePrimitive(name) {
        if (name in primitive) return primitive[name];
        const token = document.tokens[name];
        if (!token) {
            diagnostics.push(tokenDiagnostic('token-reference-missing', name));
            return undefined;
        }
        if (resolving.has(name)) {
            diagnostics.push(tokenDiagnostic('token-reference-cycle', name));
            return undefined;
        }
        resolving.add(name);
        const value = resolveValue(token.$value, resolvePrimitive);
        resolving.delete(name);
        if (value !== undefined) primitive[name] = value;
        return value;
    }

    for (const name of Object.keys(document.tokens).sort()) resolvePrimitive(name);

    const semantic = Object.create(null);
    for (const name of Object.keys(authoredOverlay.semanticTokens ?? {}).sort()) {
        const value = resolveValue(authoredOverlay.semanticTokens[name], resolvePrimitive);
        if (value === undefined) {
            diagnostics.push(tokenDiagnostic('semantic-token-unresolved', name));
        } else {
            semantic[name] = value;
        }
    }

    const theme = Object.create(null);
    for (const name of Object.keys(authoredOverlay.themeOverrides ?? {}).sort()) {
        const value = resolveValue(authoredOverlay.themeOverrides[name], (reference) => {
            if (reference in semantic) return semantic[reference];
            return resolvePrimitive(reference);
        });
        if (value === undefined) {
            diagnostics.push(tokenDiagnostic('theme-token-unresolved', name));
        } else {
            theme[name] = value;
        }
    }

    return {
        diagnostics: diagnostics.sort((a, b) => a.path.localeCompare(b.path)),
        primitive: canonicalize(primitive),
        semantic: canonicalize(semantic),
        theme: canonicalize(theme),
    };
}

function resolveValue(value, lookup) {
    if (typeof value === 'string') {
        const exact = /^\{([^{}]+)\}$/.exec(value);
        if (exact) return lookup(exact[1].trim());
        let missing = false;
        const replaced = value.replace(TOKEN_REFERENCE, (_match, reference) => {
            const resolved = lookup(reference.trim());
            if (resolved === undefined || (typeof resolved === 'object' && resolved !== null)) {
                missing = true;
                return '';
            }
            return String(resolved);
        });
        return missing ? undefined : replaced;
    }
    if (Array.isArray(value)) {
        const items = value.map((item) => resolveValue(item, lookup));
        return items.some((item) => item === undefined) ? undefined : items;
    }
    if (value && typeof value === 'object') {
        const output = {};
        for (const [key, item] of Object.entries(value)) {
            const resolved = resolveValue(item, lookup);
            if (resolved === undefined) return undefined;
            output[key] = resolved;
        }
        return output;
    }
    return value;
}

function tokenDiagnostic(code, name) {
    return {
        code,
        disposition: 'unsupported',
        message: `Token '${name}' could not be resolved`,
        path: `$.tokens.${name}`,
        severity: 'error',
    };
}

export function diffTokens(previous, next) {
    const names = new Set([...Object.keys(previous), ...Object.keys(next)]);
    const changes = [];
    for (const name of [...names].sort()) {
        if (!(name in previous)) {
            changes.push({ kind: 'added', name, value: next[name] });
        } else if (!(name in next)) {
            changes.push({ kind: 'removed', name, value: previous[name] });
        } else if (JSON.stringify(previous[name]) !== JSON.stringify(next[name])) {
            changes.push({ kind: 'changed', name, previous: previous[name], next: next[name] });
        }
    }
    return changes;
}
