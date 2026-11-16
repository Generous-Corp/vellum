import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { DesignIRValidationError, normalizeImport, validateDesignIR } from '../src/index.js';

const fixtures = new URL('../../../fixtures/design-ir/', import.meta.url);
const schemaUrl = new URL('../schema/design-ir-v1.schema.json', import.meta.url);

async function source() {
    return JSON.parse(await readFile(new URL('revision-a.source.json', fixtures), 'utf8'));
}

async function schema() {
    return JSON.parse(await readFile(schemaUrl, 'utf8'));
}

const glass = {
    id: 'vellum.frosted-glass',
    values: { blur: 12, offset: [1, 2], tint: { red: 1, green: 1, blue: 1, alpha: 0.2 } },
};

function withMaterial(design, material, schemaVersion = 3) {
    const copy = structuredClone(design);
    copy.schemaVersion = schemaVersion;
    copy.root.children[0].material = material;
    return copy;
}

// A JSON Schema evaluator for the keywords the DesignIR schema uses, so the
// published schema itself is exercised rather than only described.
function conforms(root, value, node = root) {
    if (node === true) return true;
    if (node === false) return false;
    if (node.$ref !== undefined) {
        const target = node.$ref.replace('#/$defs/', '');
        if (!conforms(root, value, root.$defs[target])) return false;
    }
    const isObject = value !== null && typeof value === 'object' && !Array.isArray(value);
    if (node.type !== undefined) {
        const types = { object: isObject, array: Array.isArray(value), string: typeof value === 'string',
            number: typeof value === 'number', integer: Number.isInteger(value),
            boolean: typeof value === 'boolean', null: value === null };
        const allowed = Array.isArray(node.type) ? node.type : [node.type];
        if (!allowed.some((type) => types[type])) return false;
    }
    if (node.const !== undefined && JSON.stringify(node.const) !== JSON.stringify(value)) return false;
    if (node.enum !== undefined && !node.enum.some((entry) => JSON.stringify(entry) === JSON.stringify(value))) {
        return false;
    }
    if (typeof value === 'string') {
        if (node.minLength !== undefined && value.length < node.minLength) return false;
        if (node.pattern !== undefined && !new RegExp(node.pattern, 'u').test(value)) return false;
    }
    if (typeof value === 'number' && node.minimum !== undefined && value < node.minimum) return false;
    if (Array.isArray(value)) {
        if (node.minItems !== undefined && value.length < node.minItems) return false;
        if (node.maxItems !== undefined && value.length > node.maxItems) return false;
        if (node.items !== undefined && !value.every((item) => conforms(root, item, node.items))) return false;
    }
    if (isObject) {
        for (const key of node.required ?? []) if (!Object.hasOwn(value, key)) return false;
        for (const [key, entry] of Object.entries(value)) {
            if (node.propertyNames !== undefined && !conforms(root, key, node.propertyNames)) return false;
            if (node.properties && Object.hasOwn(node.properties, key)) {
                if (!conforms(root, entry, node.properties[key])) return false;
            } else if (node.additionalProperties !== undefined &&
                !conforms(root, entry, node.additionalProperties)) {
                return false;
            }
        }
    }
    if (node.allOf !== undefined && !node.allOf.every((part) => conforms(root, value, part))) return false;
    if (node.not !== undefined && conforms(root, value, node.not)) return false;
    if (node.oneOf !== undefined && node.oneOf.filter((part) => conforms(root, value, part)).length !== 1) {
        return false;
    }
    if (node.if !== undefined) {
        const branch = conforms(root, value, node.if) ? node.then : node.else;
        if (branch !== undefined && !conforms(root, value, branch)) return false;
    }
    return true;
}

test('a schema version 1 document without materials stays legal', async () => {
    const design = normalizeImport(await source());
    assert.equal(design.schemaVersion, 1);
    assert.deepEqual(validateDesignIR(design), { issues: [], valid: true });
    assert.equal(conforms(await schema(), design), true);
});

test('a material reference needs schema version 3', async () => {
    const design = normalizeImport(await source());
    const published = await schema();
    for (const version of [1, 2, 3, 4]) {
        const document = withMaterial(design, glass, version);
        const result = validateDesignIR(document);
        assert.equal(result.valid, version >= 3, `validator at version ${version}`);
        if (version < 3) assert.ok(result.issues.some((entry) => entry.code === 'version'));
        assert.equal(conforms(published, document), version >= 3, `schema at version ${version}`);
    }
});

test('versions 1 to 4 are read and 0, 5 and 1.5 are refused', async () => {
    const design = normalizeImport(await source());
    const published = await schema();
    for (const version of [1, 2, 3, 4]) {
        const document = structuredClone(design);
        document.schemaVersion = version;
        assert.deepEqual(validateDesignIR(document), { issues: [], valid: true }, `${version}`);
        assert.equal(conforms(published, document), true, `schema at ${version}`);
        assert.equal(normalizeImport(await source(), { schemaVersion: version }).schemaVersion, version);
    }
    for (const version of [0, 5, 1.5]) {
        const document = structuredClone(design);
        document.schemaVersion = version;
        const result = validateDesignIR(document);
        assert.equal(result.valid, false, `${version}`);
        assert.ok(result.issues.some((entry) => entry.path === '$.schemaVersion'));
        assert.equal(conforms(published, document), false, `schema at ${version}`);
        await assert.rejects(async () => normalizeImport(await source(), { schemaVersion: version }), TypeError);
    }
});

test('compositing stays legal at version 3 and a document with both is version 3', async () => {
    const input = await source();
    input.root.children[0].compositing = { blendMode: 'multiply' };
    assert.equal(normalizeImport(input).schemaVersion, 2);
    const atThree = normalizeImport(input, { schemaVersion: 3 });
    assert.deepEqual(validateDesignIR(atThree), { issues: [], valid: true });
    assert.equal(conforms(await schema(), atThree), true);
    input.root.children[0].material = { id: 'vellum.frosted-glass', values: { blur: 4 } };
    const both = normalizeImport(input);
    assert.equal(both.schemaVersion, 3);
    assert.equal(conforms(await schema(), both), true);
    assert.throws(() => normalizeImport(input, { schemaVersion: 2 }), DesignIRValidationError);
});

test('negative control: without the conditional the schema accepts a version 1 material', async () => {
    const published = await schema();
    const document = withMaterial(normalizeImport(await source()), glass, 1);
    assert.equal(conforms(published, document), false);
    const unguarded = structuredClone(published);
    delete unguarded.allOf;
    assert.equal(conforms(unguarded, document), true);
});

test('normalization refuses a material below version 3 and keeps it at 3', async () => {
    const input = await source();
    input.root.children[0].material = { values: { blur: 4 }, id: 'vellum.frosted-glass' };
    assert.throws(() => normalizeImport(input, { schemaVersion: 1 }), DesignIRValidationError);
    assert.throws(() => normalizeImport(input, { schemaVersion: 2 }), DesignIRValidationError);
    assert.equal(normalizeImport(input).schemaVersion, 3);
    const design = normalizeImport(input, { schemaVersion: 3 });
    assert.equal(design.schemaVersion, 3);
    assert.deepEqual(design.root.children[0].material, { id: 'vellum.frosted-glass', values: { blur: 4 } });
    assert.deepEqual(validateDesignIR(design), { issues: [], valid: true });
    assert.equal(conforms(await schema(), design), true);
});

test('a material reference carries values only and refuses malformed ones', async () => {
    const design = normalizeImport(await source());
    assert.deepEqual(validateDesignIR(withMaterial(design, glass)), { issues: [], valid: true });
    const cases = [
        [{ id: 'vellum.frosted-glass', values: {}, uniforms: [] }, 'unknown-field'],
        [{ id: 'Vellum Glass', values: {} }, 'format'],
        [{ id: 'vellum.frosted-glass' }, 'type'],
        [{ id: 'vellum.frosted-glass', values: { blur: '12' } }, 'type'],
        [{ id: 'vellum.frosted-glass', values: { blur: [1, 2, 3] } }, 'type'],
        [{ id: 'vellum.frosted-glass', values: { tint: { red: 1, green: 1, blue: 1 } } }, 'type'],
        [{ id: 'vellum.frosted-glass', values: { 'two words': 1 } }, 'format'],
    ];
    const published = await schema();
    for (const [material, code] of cases) {
        const document = withMaterial(design, material);
        const result = validateDesignIR(document);
        assert.equal(result.valid, false, JSON.stringify(material));
        assert.ok(result.issues.some((entry) => entry.code === code), JSON.stringify(result.issues));
        assert.equal(conforms(published, document), false, `schema: ${JSON.stringify(material)}`);
    }
});
