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

test('a version 1 document refuses compositing, and version 3 remains readable', () => {
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

    // Versions are cumulative: every later known version keeps compositing.
    for (const version of [3, 4, 5]) {
        const later = structuredClone(document);
        later.schemaVersion = version;
        assert.deepEqual(validateDesignIR(later), { issues: [], valid: true }, `version ${version}`);
    }

    const newer = structuredClone(document);
    newer.schemaVersion = 6;
    assert.equal(issuesOf(newer)[0].path, '$.schemaVersion');
    const accepted = structuredClone(document);
    accepted.schemaVersion = 3;
    assert.deepEqual(validateDesignIR(accepted), { issues: [], valid: true });

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

// A small JSON Schema 2020-12 evaluator for the keywords the DesignIR schema
// uses, so the published schema itself is exercised rather than only read.
function schemaAccepts(root, value) {
    const resolve = (reference) => reference.slice(2).split('/').reduce((node, key) => node[key], root);
    const typeOf = (item) => {
        if (item === null) return 'null';
        if (Array.isArray(item)) return 'array';
        if (Number.isInteger(item)) return 'integer';
        return typeof item;
    };
    const typed = (item, type) =>
        typeOf(item) === type || (type === 'number' && typeOf(item) === 'integer');
    const object = (item) => typeOf(item) === 'object';
    const check = (node, item) => {
        if (node === true) return true;
        if (node === false) return false;
        if (node.$ref !== undefined && !check(resolve(node.$ref), item)) return false;
        if (node.type !== undefined &&
            ![node.type].flat().some((type) => typed(item, type))) return false;
        if ('const' in node && JSON.stringify(node.const) !== JSON.stringify(item)) return false;
        if (node.enum !== undefined &&
            !node.enum.some((entry) => JSON.stringify(entry) === JSON.stringify(item))) return false;
        if (typeof item === 'number') {
            if (node.minimum !== undefined && item < node.minimum) return false;
            if (node.maximum !== undefined && item > node.maximum) return false;
        }
        if (typeof item === 'string') {
            if (node.minLength !== undefined && [...item].length < node.minLength) return false;
            if (node.pattern !== undefined && !new RegExp(node.pattern, 'u').test(item)) return false;
        }
        if (object(item)) {
            for (const key of node.required ?? []) if (!Object.hasOwn(item, key)) return false;
            for (const [key, entry] of Object.entries(item)) {
                if (node.properties && Object.hasOwn(node.properties, key)) {
                    if (!check(node.properties[key], entry)) return false;
                } else if (node.additionalProperties !== undefined &&
                    !check(node.additionalProperties, entry)) {
                    return false;
                }
            }
        }
        if (Array.isArray(item) && node.items !== undefined &&
            !item.every((entry) => check(node.items, entry))) return false;
        if (node.allOf && !node.allOf.every((entry) => check(entry, item))) return false;
        if (node.anyOf && !node.anyOf.some((entry) => check(entry, item))) return false;
        if (node.oneOf && node.oneOf.filter((entry) => check(entry, item)).length !== 1) return false;
        if (node.not !== undefined && check(node.not, item)) return false;
        if (node.if !== undefined) {
            const branch = check(node.if, item) ? node.then : node.else;
            if (branch !== undefined && !check(branch, item)) return false;
        }
        return true;
    };
    return check(root, value);
}

test('the published schema refuses compositing in a version 1 document', () => {
    const nested = normalizeImport(sourceWith({}, [{
        kind: 'view',
        sourceId: 'outer',
        children: [child('blended', { blendMode: 'multiply' })],
    }]));
    assert.equal(nested.schemaVersion, 2);
    assert.equal(schemaAccepts(schema, nested), true, 'a version 2 document with compositing');
    const plain = normalizeImport(sourceWith({}, [child('plain', undefined)]));
    assert.equal(plain.schemaVersion, 1);
    assert.equal(schemaAccepts(schema, plain), true, 'a version 1 document without compositing');
    const promoted = structuredClone(plain);
    promoted.schemaVersion = 2;
    assert.equal(schemaAccepts(schema, promoted), true, 'version 2 does not require compositing');

    const older = structuredClone(nested);
    older.schemaVersion = 1;
    assert.equal(schemaAccepts(schema, older), false, 'compositing two levels down at version 1');
    const atRoot = structuredClone(plain);
    atRoot.root.compositing = { opacity: 0.5 };
    assert.equal(schemaAccepts(schema, atRoot), false, 'compositing on the root at version 1');
    assert.equal(validateDesignIR(older).valid, false);
    assert.equal(validateDesignIR(atRoot).valid, false);

    // Negative control: without the conditional the same evaluator accepts
    // the version 1 document, so the refusal comes from the conditional.
    const unconditional = structuredClone(schema);
    delete unconditional.allOf;
    assert.equal(schemaAccepts(unconditional, older), true);
    assert.equal(schemaAccepts(unconditional, atRoot), true);
    const unknown = structuredClone(nested);
    unknown.root.children[0].children[0].compositing.blendMode = 'spectral';
    assert.equal(schemaAccepts(schema, unknown), false, 'the evaluator checks nested enums');
});

test('a mask node takes no blend mode, in the validator and the published schema', () => {
    const masked = normalizeImport(sourceWith({}, [
        child('content', { maskRef: 'screen/cutout', blendMode: 'multiply' }),
        child('cutout', { mask: 'alpha' }),
    ]));
    assert.deepEqual(validateDesignIR(masked), { issues: [], valid: true });
    assert.equal(schemaAccepts(schema, masked), true, 'a blended sibling of a mask');
    for (const mode of ['multiply', 'destination_in', 'hue']) {
        assert.match(
            refusal(() => normalizeImport(sourceWith({}, [
                child('content', { maskRef: 'screen/cutout' }),
                child('cutout', { mask: 'alpha', blendMode: mode }),
            ]))),
            /compositing\.blendMode: a mask node takes no blend mode but normal/,
            mode,
        );
        const document = structuredClone(masked);
        document.root.children[1].compositing.blendMode = mode;
        assert.deepEqual(
            issuesOf(document).map(({ code, path }) => ({ code, path })),
            [{ code: 'mask', path: '$.root.children[1].compositing.blendMode' }],
            mode,
        );
        assert.equal(schemaAccepts(schema, document), false, mode);
    }
    const explicit = normalizeImport(sourceWith({}, [
        child('content', { maskRef: 'screen/cutout' }),
        child('cutout', { mask: 'alpha', blendMode: 'normal' }),
    ]));
    assert.deepEqual(explicit.root.children[1].compositing, { mask: 'alpha' });
});
