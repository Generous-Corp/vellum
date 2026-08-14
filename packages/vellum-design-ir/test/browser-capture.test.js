import assert from 'node:assert/strict';
import test from 'node:test';
import {
    BROWSER_CAPTURE_ENVELOPE_SCHEMA,
    lowerBrowserCaptureToDesignIR,
    validateBrowserCaptureEnvelope,
} from '../src/index.js';

function envelope(overrides = {}) {
    return {
        schema: BROWSER_CAPTURE_ENVELOPE_SCHEMA,
        captureId: 'capture-001',
        source: {
            url: 'http://127.0.0.1:8000/',
            browser: 'Chrome',
            browserVersion: '151.0.7922.47',
            plan: 'smoke',
        },
        viewport: { width: 640, height: 400 },
        root: { kind: 'view', semanticId: 'root', name: 'Root', children: [
            { kind: 'text', semanticId: 'title', role: 'heading', text: 'Roadmap', properties: {} },
        ] },
        assets: [],
        diagnostics: [],
        ...overrides,
    };
}

test('browser capture envelope lowers deterministically to canonical DesignIR', () => {
    const first = lowerBrowserCaptureToDesignIR(envelope());
    const second = lowerBrowserCaptureToDesignIR(envelope());
    assert.deepEqual(first, second);
    assert.equal(first.source.adapter, 'vellum-browser-capture');
    assert.equal(first.source.sourceUri, 'http://127.0.0.1:8000/');
    assert.equal(first.root.children[0].text, 'Roadmap');
});

test('browser capture envelope rejects unknown fields and unbounded metadata', () => {
    assert.throws(() => validateBrowserCaptureEnvelope({ ...envelope(), extra: true }), /unknown fields/);
    assert.throws(() => validateBrowserCaptureEnvelope({
        ...envelope(), viewport: { width: 0, height: 400 },
    }), /viewport/);
    assert.throws(() => validateBrowserCaptureEnvelope({
        ...envelope(), diagnostics: Array.from({ length: 257 }, () => ({})),
    }), /bounded arrays/);
});
