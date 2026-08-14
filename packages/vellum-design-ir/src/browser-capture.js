import { normalizeImport } from './normalize.js';

export const BROWSER_CAPTURE_ENVELOPE_SCHEMA = 'vellum.browser-capture-envelope.v1';
const MAX_CAPTURE_ID = 128;
const MAX_URL = 4096;
const MAX_DIAGNOSTICS = 256;

function text(value, field, maximum) {
    if (typeof value !== 'string' || value.length === 0 || value.length > maximum || value.includes('\0')) {
        throw new TypeError(`${field} must be a bounded non-empty string`);
    }
    return value;
}

function viewport(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value) ||
        !Number.isInteger(value.width) || !Number.isInteger(value.height) ||
        value.width <= 0 || value.width > 16384 || value.height <= 0 || value.height > 16384) {
        throw new TypeError('capture.viewport must contain bounded integer width and height');
    }
    return { width: value.width, height: value.height };
}

/** Validate a browser capture envelope without reading files or using a clock. */
export function validateBrowserCaptureEnvelope(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value) ||
        value.schema !== BROWSER_CAPTURE_ENVELOPE_SCHEMA) {
        throw new TypeError(`capture envelope schema must be ${BROWSER_CAPTURE_ENVELOPE_SCHEMA}`);
    }
    const allowed = new Set(['schema', 'captureId', 'source', 'viewport', 'root', 'assets', 'diagnostics']);
    if (Object.keys(value).some((key) => !allowed.has(key))) throw new TypeError('capture envelope contains unknown fields');
    const source = value.source;
    if (!source || typeof source !== 'object' || Array.isArray(source) ||
        Object.keys(source).some((key) => !new Set(['url', 'browser', 'browserVersion', 'plan']).has(key))) {
        throw new TypeError('capture.source is malformed');
    }
    const normalized = {
        schema: BROWSER_CAPTURE_ENVELOPE_SCHEMA,
        captureId: text(value.captureId, 'capture.captureId', MAX_CAPTURE_ID),
        source: {
            url: text(source.url, 'capture.source.url', MAX_URL),
            browser: text(source.browser, 'capture.source.browser', 128),
            browserVersion: text(source.browserVersion, 'capture.source.browserVersion', 128),
        },
        viewport: viewport(value.viewport),
        root: value.root,
        assets: value.assets ?? [],
        diagnostics: value.diagnostics ?? [],
    };
    if (!normalized.root || typeof normalized.root !== 'object' || Array.isArray(normalized.root)) {
        throw new TypeError('capture.root must be an object');
    }
    if (!Array.isArray(normalized.assets) || !Array.isArray(normalized.diagnostics) ||
        normalized.diagnostics.length > MAX_DIAGNOSTICS) {
        throw new TypeError('capture assets and diagnostics must be bounded arrays');
    }
    if (source.plan !== undefined) normalized.source.plan = text(source.plan, 'capture.source.plan', 256);
    return normalized;
}

/** Lower a validated browser envelope through the canonical DesignIR path. */
export function lowerBrowserCaptureToDesignIR(value) {
    const envelope = validateBrowserCaptureEnvelope(value);
    return normalizeImport({
        source: {
            key: 'browser',
            namespace: 'browser',
            adapter: 'vellum-browser-capture',
            adapterVersion: '1',
            formatVersion: 'vellum.browser-capture-envelope.v1',
            revision: envelope.captureId,
            sourceUri: envelope.source.url,
            provenance: {
                browser: envelope.source.browser,
                browserVersion: envelope.source.browserVersion,
                viewport: envelope.viewport,
                plan: envelope.source.plan ?? null,
            },
        },
        root: envelope.root,
        assets: envelope.assets,
        diagnostics: envelope.diagnostics,
    });
}
