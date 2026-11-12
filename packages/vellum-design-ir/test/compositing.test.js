import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import {
    BLEND_MODES,
    BLEND_MODES_TIER_1,
    BLEND_MODES_TIER_2,
    DESIGN_IR_COMPOSITING_VERSION,
    DESIGN_IR_VERSION,
    DESIGN_IR_VERSIONS,
    DesignIRValidationError,
    MASK_MODES,
    blendModeTier,
    emptyAuthoredOverlay,
    normalizeImport,
    parseDesignIR,
    reimportDesign,
    stableStringify,
    validateDesignIR,
} from '../src/index.js';

const schema = JSON.parse(
    await readFile(new URL('../schema/design-ir-v1.schema.json', import.meta.url), 'utf8'),
);

function sourceWith(rootExtras = {}, children = []) {
    return {
        source: {
            key: 'screen',
            adapter: 'fixture',
            adapterVersion: '1',
            formatVersion: '1',
            revision: 'a',
        },
        root: { kind: 'view', sourceId: 'root', ...rootExtras, children },
    };
}

function child(sourceId, compositing) {
    return { kind: 'view', sourceId, compositing, children: [] };
}

// What a refusal says: the error's message and, for a validation error, each issue.
function refusal(action) {
    try {
        action();
    } catch (error) {
        return [error.message, ...(error.issues ?? []).map((entry) => entry.message)].join('\n');
    }
    assert.fail('the document was accepted');
}

function issuesOf(document) {
    return validateDesignIR(document).issues;
}

test('the blend vocabulary has eleven Tier 1 and eighteen Tier 2 modes', () => {
    assert.equal(BLEND_MODES_TIER_1.length, 11);
    assert.equal(BLEND_MODES_TIER_2.length, 18);
    assert.equal(new Set(BLEND_MODES).size, 29);
    assert.deepEqual(MASK_MODES, ['alpha', 'luminance']);
    for (const mode of BLEND_MODES_TIER_1) assert.equal(blendModeTier(mode), 1, mode);
    for (const mode of BLEND_MODES_TIER_2) assert.equal(blendModeTier(mode), 2, mode);
    assert.equal(blendModeTier('spectral'), 0);
    assert.equal(blendModeTier('Multiply'), 0);
    assert.deepEqual(
        BLEND_MODES_TIER_1.slice(0, 3),
        ['normal', 'multiply', 'screen'],
    );
});

test('the published schema lists the same versions and blend modes as the validator', () => {
    assert.deepEqual(schema.properties.schemaVersion.enum, [...DESIGN_IR_VERSIONS]);
    assert.deepEqual(schema.$defs.blendMode.enum, [...BLEND_MODES]);
    assert.deepEqual(schema.$defs.compositing.properties.mask.enum, [...MASK_MODES]);
    assert.deepEqual(
        Object.keys(schema.$defs.compositing.properties).sort(),
        ['blendMode', 'isolate', 'mask', 'maskRef', 'opacity'],
    );
    assert.equal(schema.$defs.compositing.additionalProperties, false);
    assert.deepEqual(schema.$defs.node.properties.compositing, { $ref: '#/$defs/compositing' });
});

test('a document with no compositing stays at schema version 1', () => {
    const document = normalizeImport(sourceWith());
    assert.equal(document.schemaVersion, DESIGN_IR_VERSION);
    assert.equal(Object.hasOwn(document.root, 'compositing'), false);
    const defaults = normalizeImport(sourceWith({}, [
        child('plain', { opacity: 1, blendMode: 'normal', isolate: false }),
    ]));
    assert.equal(defaults.schemaVersion, DESIGN_IR_VERSION);
    assert.equal(Object.hasOwn(defaults.root.children[0], 'compositing'), false);
});

test('every blend mode normalizes into a version 2 document and validates', () => {
    for (const mode of BLEND_MODES.filter((name) => name !== 'normal')) {
        const document = normalizeImport(sourceWith({}, [child('blended', { blendMode: mode })]));
        assert.equal(document.schemaVersion, DESIGN_IR_COMPOSITING_VERSION, mode);
        assert.deepEqual(document.root.children[0].compositing, { blendMode: mode });
        assert.deepEqual(validateDesignIR(document), { issues: [], valid: true });
        assert.equal(
            stableStringify(parseDesignIR(stableStringify(document))),
            stableStringify(document),
        );
    }
});

test('compositing carries opacity, isolation and masking and normalizes deterministically', () => {
    const build = (flipped) => {
        const compositing = { opacity: 0.5, blendMode: 'overlay', isolate: true };
        return sourceWith({}, [
            child('content', flipped ? Object.fromEntries(Object.entries(compositing).reverse()) : compositing),
            child('cutout', { mask: 'luminance' }),
        ]);
    };
    const first = normalizeImport(build(false));
    const second = normalizeImport(build(true));
    assert.equal(stableStringify(first), stableStringify(second));
    assert.deepEqual(first.root.children[0].compositing, {
        blendMode: 'overlay',
        isolate: true,
        opacity: 0.5,
    });
    assert.deepEqual(first.root.children[1].compositing, { mask: 'luminance' });
    assert.deepEqual(validateDesignIR(first), { issues: [], valid: true });
});

test('a mask reference names a sibling mask', () => {
    const document = normalizeImport(sourceWith({}, [
        child('content', { maskRef: 'screen/cutout' }),
        child('cutout', { mask: 'alpha' }),
    ]));
    assert.equal(document.root.children[0].compositing.maskRef, 'screen/cutout');
    assert.deepEqual(validateDesignIR(document), { issues: [], valid: true });
});

test('invalid compositing fails closed with the offending path', () => {
    const rejects = (compositing, pattern, siblings = []) =>
        assert.match(
            refusal(() => normalizeImport(sourceWith({}, [child('bad', compositing), ...siblings]))),
            pattern,
        );
    rejects({ blendMode: 'spectral' }, /compositing\.blendMode: blendMode must be a known blend mode/);
    rejects({ blendMode: 'Multiply' }, /blendMode must be a known blend mode/);
    rejects({ blendMode: 'color-dodge' }, /blendMode must be a known blend mode/);
    rejects({ opacity: 1.5 }, /opacity must be a number from 0 to 1/);
    rejects({ opacity: -0.1 }, /opacity must be a number from 0 to 1/);
    rejects({ opacity: Number.NaN }, /opacity must be a number from 0 to 1/);
    rejects({ opacity: '0.5' }, /opacity must be a number from 0 to 1/);
    rejects({ isolate: 'yes' }, /isolate must be a boolean/);
    rejects({ mask: 'inverted' }, /mask must be alpha or luminance/);
    rejects({ maskRef: '' }, /maskRef must be a non-empty string/);
    rejects({ futureBlend: 'x' }, /unknown compositing field 'futureBlend'/);
    rejects('multiply', /must be an object/);
    rejects(['multiply'], /must be an object/);
    rejects({ maskRef: 'screen/missing' }, /maskRef 'screen\/missing' is not a sibling/);
    rejects({ maskRef: 'screen/bad' }, /a node cannot be its own mask/);
    rejects({ maskRef: 'screen/plain' }, /does not name a mask node/, [child('plain', {})]);
    rejects({ mask: 'alpha', maskRef: 'screen/other' }, /cannot reference another mask/, [
        child('other', { mask: 'alpha' }),
    ]);
    assert.match(
        refusal(() => normalizeImport(sourceWith({ compositing: { mask: 'alpha' } }))),
        /the root has no siblings to mask/,
    );
    assert.match(
        refusal(() => normalizeImport(sourceWith({ compositing: { maskRef: 'screen/root' } }))),
        /the root has no siblings to be masked by/,
    );
});

test('a mask reference to a cousin is not a sibling', () => {
    assert.match(
        refusal(() => normalizeImport(sourceWith({}, [
            { kind: 'view', sourceId: 'left', children: [child('cutout', { mask: 'alpha' })] },
            { kind: 'view', sourceId: 'right', children: [child('content', { maskRef: 'screen/cutout' })] },
        ]))),
        /maskRef 'screen\/cutout' is not a sibling/,
    );
});

test('a version 1 document refuses compositing, and unknown versions stay refused', () => {
    const document = normalizeImport(sourceWith({}, [child('blended', { blendMode: 'multiply' })]));
    assert.equal(document.schemaVersion, 2);
    const older = structuredClone(document);
    older.schemaVersion = 1;
    const problems = issuesOf(older);
    assert.deepEqual(
        problems.map(({ code, path }) => ({ code, path })),
        [{ code: 'version', path: '$.root.children[0].compositing' }],
    );
    assert.throws(() => parseDesignIR(older), DesignIRValidationError);

    const newer = structuredClone(document);
    newer.schemaVersion = 3;
    assert.equal(issuesOf(newer)[0].path, '$.schemaVersion');

    const plain = normalizeImport(sourceWith());
    plain.schemaVersion = 2;
    assert.deepEqual(validateDesignIR(plain), { issues: [], valid: true });

    const valid = structuredClone(document);
    assert.deepEqual(validateDesignIR(valid), { issues: [], valid: true });
    valid.root.children[0].compositing.blendMode = 'spectral';
    assert.deepEqual(
        issuesOf(valid).map(({ code, path }) => ({ code, path })),
        [{ code: 'enum', path: '$.root.children[0].compositing.blendMode' }],
    );
});

test('compositing is part of what a reimport compares', () => {
    const previous = normalizeImport(sourceWith({}, [child('blended', undefined)]));
    const next = normalizeImport(sourceWith({}, [child('blended', { blendMode: 'multiply' })]));
    assert.equal(previous.schemaVersion, 1);
    assert.equal(next.schemaVersion, 2);
    const result = reimportDesign(previous, next, emptyAuthoredOverlay('screen'));
    assert.equal(result.accepted, true);
    assert.deepEqual(
        result.report.changes.changed.find(({ id }) => id === 'screen/blended').fields,
        ['compositing'],
    );
    const unchanged = reimportDesign(next, next, emptyAuthoredOverlay('screen'));
    assert.deepEqual(unchanged.report.changes.changed, []);
});
