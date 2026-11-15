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
    copy.schemaVersion = 4;
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
        [[{ property: 'opacity', index: 1, keyframes: [{ time: 0, value: 0 }] }], 'unknown-field'],
        [[{ property: 'shadowColor', stop: 1, keyframes: [{ time: 0, value: '#000000' }] }], 'unknown-field'],
        [opacity([{ time: 0, value: 1e100 }]), 'type'],
        [opacity([{ time: 1e100, value: 0 }]), 'type'],
        [opacity([{ time: 0, value: 0, easing: { type: 'steps', steps: 1, position: 'none' } }]), 'range'],
        [opacity([{ time: 0, value: 0, easing: { type: 'spring', stiffness: 100, damping: 10 } }, { time: 1, value: 1 }]), 'range'],
    ];
    for (const [tracks, code] of cases) {
        const result = validateDesignIR(withTracks(design, tracks));
        assert.equal(result.valid, false, JSON.stringify(tracks));
        assert.ok(result.issues.some((entry) => entry.code === code), JSON.stringify(result.issues));
    }
});

test('a spring segment that lasts its settle time is accepted', async () => {
    const design = normalizeImport(await source());
    const tracks = [{
        property: 'opacity',
        keyframes: [
            { time: 0, value: 0, easing: { type: 'spring', stiffness: 100, damping: 10 } },
            { time: 2.34, value: 1 },
        ],
    }];
    assert.deepEqual(validateDesignIR(withTracks(design, tracks)), { issues: [], valid: true });
    tracks[0].keyframes[1].time = 2.3;
    assert.equal(validateDesignIR(withTracks(design, tracks)).valid, false);
});

test('every version up to 4 is read, and tracks need version 4', async () => {
    const design = normalizeImport(await source());
    const definitions = JSON.parse(await readFile(schema, 'utf8'));
    for (const version of [1, 2, 3, 4]) {
        const plain = { ...design, schemaVersion: version };
        assert.deepEqual(validateDesignIR(plain), { issues: [], valid: true }, `version ${version}`);
        assert.equal(conforms(definitions, plain, definitions.$defs), true, `schema version ${version}`);
        const animated = { ...withTracks(design, pulse), schemaVersion: version };
        assert.equal(validateDesignIR(animated).valid, version === 4, `tracks at ${version}`);
        assert.equal(conforms(definitions, animated, definitions.$defs), version === 4, `schema tracks at ${version}`);
    }
    for (const version of [0, 5, 1.5]) {
        assert.equal(validateDesignIR({ ...design, schemaVersion: version }).valid, false, `version ${version}`);
        assert.equal(conforms(definitions, { ...design, schemaVersion: version }, definitions.$defs), false);
    }
});

test('tracks need schema version 4 while older documents stay valid', async () => {
    const design = normalizeImport(await source());
    assert.equal(design.schemaVersion, 1);
    assert.deepEqual(validateDesignIR(design), { issues: [], valid: true });
    const current = withTracks(design, pulse);
    assert.deepEqual(validateDesignIR(current), { issues: [], valid: true });
    for (const version of [1, 2, 3]) {
        const older = { ...current, schemaVersion: version };
        const result = validateDesignIR(older);
        assert.equal(result.valid, false, `version ${version}`);
        assert.ok(result.issues.some((entry) => entry.code === 'version'), JSON.stringify(result.issues));
    }
    assert.equal(validateDesignIR({ ...design, schemaVersion: 4 }).valid, true);
    const definitions = JSON.parse(await readFile(schema, 'utf8'));
    assert.equal(conforms(definitions, current, definitions.$defs), true);
    assert.equal(conforms(definitions, { ...current, schemaVersion: 1 }, definitions.$defs), false);
    assert.equal(conforms(definitions, design, definitions.$defs), true);
});

test('normalization keeps a source node tracks', async () => {
    const raw = await source();
    raw.root.tracks = structuredClone(pulse);
    const design = normalizeImport(raw);
    assert.equal(design.schemaVersion, 4);
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

// A minimal evaluator for the JSON Schema keywords the animation definitions
// use, so the schema and the validator can be checked against each other.
function conforms(schema, value, definitions) {
    if (typeof schema === 'boolean') return schema;
    if (schema.$ref) return conforms(definitions[schema.$ref.replace('#/$defs/', '')], value, definitions);
    const isObject = (item) => item !== null && typeof item === 'object' && !Array.isArray(item);
    if (schema.type === 'object' && !isObject(value)) return false;
    if (schema.type === 'array' && !Array.isArray(value)) return false;
    if (schema.type === 'string' && typeof value !== 'string') return false;
    if (schema.type === 'number' && typeof value !== 'number') return false;
    if (schema.type === 'integer' && !Number.isInteger(value)) return false;
    if (schema.enum && !schema.enum.includes(value)) return false;
    if ('const' in schema && schema.const !== value) return false;
    if (schema.pattern && !(typeof value === 'string' && new RegExp(schema.pattern).test(value))) return false;
    if (typeof value === 'number') {
        if (schema.minimum !== undefined && value < schema.minimum) return false;
        if (schema.maximum !== undefined && value > schema.maximum) return false;
        if (schema.exclusiveMinimum !== undefined && value <= schema.exclusiveMinimum) return false;
    }
    if (Array.isArray(value)) {
        if (schema.minItems !== undefined && value.length < schema.minItems) return false;
        if (schema.maxItems !== undefined && value.length > schema.maxItems) return false;
        if (schema.items && !value.every((item) => conforms(schema.items, item, definitions))) return false;
    }
    if (isObject(value)) {
        if (schema.required && !schema.required.every((key) => key in value)) return false;
        for (const [key, item] of Object.entries(value)) {
            if (schema.properties && key in schema.properties) {
                if (!conforms(schema.properties[key], item, definitions)) return false;
            } else if (schema.additionalProperties === false) {
                return false;
            }
        }
    }
    if (schema.not && conforms(schema.not, value, definitions)) return false;
    if (schema.oneOf && schema.oneOf.filter((option) => conforms(option, value, definitions)).length !== 1) return false;
    if (schema.allOf && !schema.allOf.every((part) => conforms(part, value, definitions))) return false;
    if (schema.if) {
        const branch = conforms(schema.if, value, definitions) ? schema.then : schema.else;
        if (branch && !conforms(branch, value, definitions)) return false;
    }
    return true;
}

test('the schema and the validator agree on tracks', async () => {
    const definitions = JSON.parse(await readFile(schema, 'utf8')).$defs;
    const design = normalizeImport(await source());
    const opacity = (keyframes) => ({ property: 'opacity', keyframes });
    const cases = [
        ...pulse.map((track) => [track, true]),
        [{ property: 'fill', keyframes: [{ time: 0, value: 1 }] }, false],
        [opacity([{ time: 0, value: '#FFFFFF' }]), false],
        [opacity([{ time: 0, value: 0, easing: { type: 'steps', steps: 1, position: 'none' } }]), false],
        [opacity([{ time: 0, value: 0, easing: { type: 'steps', steps: 2, position: 'none' } }]), true],
        [opacity([{ time: 0, value: 1e100 }]), false],
        [{ property: 'opacity', index: 0, keyframes: [{ time: 0, value: 0 }] }, false],
        [{ property: 'textRunColor', index: 2, keyframes: [{ time: 0, value: '#00000080' }] }, true],
        [{ property: 'textRunColor', stop: 2, keyframes: [{ time: 0, value: '#000000' }] }, false],
        [opacity([{ time: 0, value: 0, foo: 1 }]), false],
        [opacity([{ time: 0, value: 0, easing: { type: 'cubicBezier', x1: 0.2, y1: 3, x2: 0.8, y2: -2 } }]), true],
    ];
    for (const [track, valid] of cases) {
        assert.equal(conforms(definitions.animationTrack, track, definitions), valid, `schema: ${JSON.stringify(track)}`);
        assert.equal(validateDesignIR(withTracks(design, [track])).valid, valid, `validator: ${JSON.stringify(track)}`);
    }
});
