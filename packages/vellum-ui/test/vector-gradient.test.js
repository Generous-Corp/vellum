import assert from 'node:assert/strict';
import test from 'node:test';

import { Vector, createApp, jsx, mount } from '../src/runtime.js';

const stops = [{ position: 0, color: '#000000' }, { position: 1, color: '#ffffff' }];

function renderGradient(gradient) {
    return () => mount(createApp({
        render: () => jsx(Vector, {
            id: 'gradient',
            asset: {
                contentHash: `sha256:${'a'.repeat(64)}`,
                viewBox: [0, 0, 10, 10],
                ops: [{ op: 'path', d: 'M0 0H10V10Z', fill: { gradient: { ...gradient, stops } } }],
            },
        }),
    })).renderJSON();
}

test('vector gradients accept native radius defaults and distinct float32 circles', () => {
    for (const kind of ['linear', 'conic', 'radial', 'two-circle-radial']) {
        const gradient = { kind, radiusX: 0, radiusY: 0, startRadius: 0, endRadius: 0.5 };
        if (kind === 'linear') gradient.angle = 90;
        assert.doesNotThrow(renderGradient(gradient), kind);
    }
    assert.doesNotThrow(renderGradient({ kind: 'radial', radiusX: 1, radiusY: 2 }));
    // Irrelevant radii that round to their native defaults remain expressible.
    assert.doesNotThrow(renderGradient({
        kind: 'linear', angle: 90, radiusX: 1e-50, startRadius: 1e-50,
        endRadius: 0.5 + 2 ** -26,
    }));
    for (const geometry of [
        { endRadius: 1 + 2 ** -23 },
        { focalX: 0.5 + 2 ** -24 },
        { focalY: 0.5 + 2 ** -24 },
    ]) {
        assert.doesNotThrow(renderGradient({
            kind: 'two-circle-radial', startRadius: 1, endRadius: 1, ...geometry,
        }));
    }
});

test('vector gradients reject radii belonging to a different gradient kind', () => {
    for (const kind of ['linear', 'conic', 'two-circle-radial']) {
        for (const radius of ['radiusX', 'radiusY']) {
            const gradient = { kind, [radius]: 1 };
            if (kind === 'linear') gradient.angle = 90;
            assert.throws(renderGradient(gradient), /radius x and y apply only to radial gradients/,
                `${kind}.${radius}`);
        }
    }
    for (const kind of ['linear', 'conic', 'radial']) {
        for (const radius of ['startRadius', 'endRadius']) {
            const gradient = { kind, [radius]: 1 };
            if (kind === 'linear') gradient.angle = 90;
            assert.throws(renderGradient(gradient), /start and end radius apply only to two-circle radial gradients/,
                `${kind}.${radius}`);
        }
    }
});

test('vector gradients reject coincident two-circle radial circles', () => {
    assert.throws(renderGradient({ kind: 'two-circle-radial', startRadius: 1, endRadius: 1 }),
        /start and end circles.*identical/);
    assert.throws(renderGradient({
        kind: 'two-circle-radial', startRadius: 0, endRadius: 0,
        focalX: 2, focalY: 3, centerX: 2, centerY: 3,
    }), /start and end circles.*identical/);
});

test('vector gradients reject circles that become identical after float32 rounding', () => {
    for (const geometry of [
        { endRadius: 1 + 2 ** -25 },
        { focalX: 0.5 + 2 ** -26 },
        { focalY: 0.5 + 2 ** -26 },
        { endRadius: 1 + 2 ** -25, focalX: 0.5 + 2 ** -26, focalY: 0.5 + 2 ** -26 },
    ]) {
        assert.throws(renderGradient({
            kind: 'two-circle-radial', startRadius: 1, endRadius: 1, ...geometry,
        }), /start and end circles.*identical/);
    }
});
