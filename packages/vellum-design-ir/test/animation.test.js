import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { normalizeImport, validateDesignIR } from '../src/index.js';

const fixtures = new URL('../../../fixtures/design-ir/', import.meta.url);
const schema = new URL('../schema/design-ir-v1.schema.json', import.meta.url);

async function source() {
    return JSON.parse(await readFile(new URL('revision-a.source.json', fixtures), 'utf8'));
}

function withTracks(design, tracks) {
    const copy = structuredClone(design);
    copy.root.tracks = tracks;
    return copy;
}

const pulse = [
    {
        property: 'opacity',
        keyframes: [
            { time: 0, value: 0, easing: 'ease' },
            { time: 1, value: 1, easing: { type: 'steps', steps: 3, position: 'start' } },
            { time: 2, value: 0.5 },
        ],
    },
    {
        property: 'translateX',
        keyframes: [
            { time: 0, value: 0, easing: { type: 'spring', stiffness: 170, damping: 26 } },
            { time: 4, value: 30 },
        ],
    },
    { property: 'fill', keyframes: [{ time: 0, value: '#FF0000' }, { time: 1, value: '#0000FF80' }] },
    { property: 'gradientStopColor', index: 0, stop: 1, keyframes: [{ time: 0, value: '#000000' }] },
];

test('a node carries animation tracks', async () => {
    const design = normalizeImport(await source());
    assert.deepEqual(validateDesignIR(withTracks(design, pulse)), { issues: [], valid: true });
});

test('tracks with malformed keyframes, easings or addresses are refused', async () => {
    const design = normalizeImport(await source());
    const opacity = (keyframes) => [{ property: 'opacity', keyframes }];
    const cases = [
        [{ property: 'opacity' }, 'type'],
        [[{ property: 'blur', keyframes: [{ time: 0, value: 1 }] }], 'enum'],
        [opacity([]), 'type'],
        [opacity([{ time: 1, value: 0 }, { time: 1, value: 1 }]), 'order'],
        [opacity([{ time: 0, value: '#FFFFFF' }]), 'type'],
        [[{ property: 'fill', keyframes: [{ time: 0, value: 1 }] }], 'type'],
        [opacity([{ time: 0, value: 0, easing: 'bounce' }]), 'enum'],
        [opacity([{ time: 0, value: 0, easing: { type: 'cubicBezier', x1: 2, y1: 0, x2: 1, y2: 1 } }]), 'range'],
        [opacity([{ time: 0, value: 0, easing: { type: 'steps', steps: 0 } }]), 'range'],
        [opacity([{ time: 0, value: 0, easing: { type: 'spring', damping: 0 } }]), 'range'],
        [opacity([{ time: 0, value: 0, clock: 3 }]), 'unknown-field'],
        [[{ property: 'shadowColor', index: -1, keyframes: [{ time: 0, value: '#000000' }] }], 'range'],
        [[...opacity([{ time: 0, value: 0 }]), ...opacity([{ time: 0, value: 1 }])], 'duplicate'],
    ];
    for (const [tracks, code] of cases) {
        const result = validateDesignIR(withTracks(design, tracks));
        assert.equal(result.valid, false, JSON.stringify(tracks));
        assert.ok(result.issues.some((entry) => entry.code === code), JSON.stringify(result.issues));
    }
});

test('normalization keeps a source node tracks', async () => {
    const raw = await source();
    raw.root.tracks = structuredClone(pulse);
    const design = normalizeImport(raw);
    assert.deepEqual(design.root.tracks[0].keyframes[1].easing, { position: 'start', steps: 3, type: 'steps' });
    assert.equal(design.root.tracks.length, pulse.length);
    assert.equal(design.root.extensions?.tracks, undefined);
    assert.deepEqual(validateDesignIR(design), { issues: [], valid: true });
});

test('the schema declares tracks with keyframes and easings', async () => {
    const definitions = JSON.parse(await readFile(schema, 'utf8')).$defs;
    assert.deepEqual(definitions.node.properties.tracks.items, { $ref: '#/$defs/animationTrack' });
    assert.deepEqual(Object.keys(definitions.animationTrack.properties), ['property', 'index', 'stop', 'keyframes']);
    assert.deepEqual(Object.keys(definitions.keyframe.properties), ['time', 'value', 'easing']);
    assert.equal(definitions.easing.oneOf.length, 4);
});
