import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import {
    DESIGN_IR_VERSIONS,
    DESIGN_IR_VECTOR_ASSET_VERSION,
    DESIGN_IR_VERSION,
    normalizeImport,
    svgAssetToDesignIR,
    validateDesignIR,
} from '../src/index.js';

const here = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(await readFile(join(here, '..', 'schema', 'design-ir-v1.schema.json'), 'utf8'));
const source = { key: 'assets', adapter: 'svg', adapterVersion: '1', formatVersion: '1.1', revision: 'test' };
const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="5" height="5" fill="#ff0000"/></svg>';

function refused(document, pattern) {
    const { valid, issues } = validateDesignIR(document);
    return !valid && issues.some((entry) => entry.path === '$.schemaVersion' && pattern.test(entry.message));
}

// The subset of JSON Schema 2020-12 the DesignIR schema uses, enough to
// check its version conditional without a validator dependency.
function matches(value, rule, root = rule) {
    if (rule === true || rule === undefined) return true;
    if (rule === false) return false;
    if (rule.$ref) {
        const target = rule.$ref.replace('#/', '').split('/').reduce((node, key) => node[key], root);
        if (!matches(value, target, root)) return false;
    }
    const type = Array.isArray(value) ? 'array' : value === null ? 'null' : typeof value;
    if (rule.type) {
        const types = [rule.type].flat();
        const ok = types.some((name) => name === type || (name === 'integer' && Number.isInteger(value)));
        if (!ok) return false;
    }
    if ('const' in rule && JSON.stringify(rule.const) !== JSON.stringify(value)) return false;
    if (rule.enum && !rule.enum.some((item) => JSON.stringify(item) === JSON.stringify(value))) return false;
    if (rule.not && matches(value, rule.not, root)) return false;
    if (rule.allOf && !rule.allOf.every((item) => matches(value, item, root))) return false;
    if (rule.anyOf && !rule.anyOf.some((item) => matches(value, item, root))) return false;
    if (rule.oneOf && rule.oneOf.filter((item) => matches(value, item, root)).length !== 1) return false;
    if (rule.if && matches(value, rule.if, root) && !matches(value, rule.then, root)) return false;
    if (type === 'object') {
        if (rule.required && !rule.required.every((key) => Object.hasOwn(value, key))) return false;
        for (const [key, item] of Object.entries(value)) {
            if (rule.properties && Object.hasOwn(rule.properties, key)) {
                if (!matches(item, rule.properties[key], root)) return false;
            } else if (rule.additionalProperties === false) {
                return false;
            } else if (typeof rule.additionalProperties === 'object' &&
                !matches(item, rule.additionalProperties, root)) {
                return false;
            }
        }
    }
    if (type === 'array' && rule.items && !value.every((item) => matches(item, rule.items, root))) {
        return false;
    }
    return true;
}

test('a document without vector asset fields stays at version 1', () => {
    const document = normalizeImport({ source, root: { kind: 'view', sourceId: 'root', children: [] } });
    assert.equal(document.schemaVersion, 1);
    assert.ok(validateDesignIR(document).valid);
    assert.ok(matches(document, schema), 'the published schema accepts it');
});

test('vector asset fields need version 6 in the validator and the published schema', () => {
    const { document } = svgAssetToDesignIR(svg, { source });
    assert.equal(DESIGN_IR_VERSION, 1);
    assert.equal(document.schemaVersion, DESIGN_IR_VECTOR_ASSET_VERSION);
    assert.doesNotThrow(() => validateDesignIR(document, { throwOnError: true }));
    assert.ok(matches(document, schema), 'the published schema accepts version 6');
    const older = { ...document, schemaVersion: 1 };
    assert.ok(refused(older, /needs schemaVersion 6/));
    assert.equal(matches(older, schema), false, 'the published schema refuses version 1');
    for (const [field, edit] of [
        ['a path kind', (node) => ({ ...node, kind: 'path', properties: {} })],
        ['a vector bucket', (node) => ({ ...node, properties: { vector: {} } })],
        ['an image source', (node) => ({ ...node, properties: { image: { source: 'x' } } })],
        ['a paint transform', (node) => ({ ...node, properties: { paint: { transform: 'none' } } })],
    ]) {
        const legacy = normalizeImport({ source, root: { kind: 'view', sourceId: 'root', children: [] } });
        const tampered = { ...legacy, root: edit(legacy.root) };
        for (const below of [1, 2, 3, 4, 5]) {
            assert.ok(refused({ ...tampered, schemaVersion: below }, /needs schemaVersion 6/), `${field} at ${below}`);
            assert.equal(matches({ ...tampered, schemaVersion: below }, schema), false, `the schema refuses ${field} at ${below}`);
        }
        assert.ok(validateDesignIR({ ...tampered, schemaVersion: 6 }).valid, `${field} is legal at version 6`);
        assert.equal(matches(tampered, schema), false, `the schema refuses ${field} at version 1`);
        assert.ok(matches({ ...tampered, schemaVersion: 6 }, schema), `the schema accepts ${field} at version 6`);
    }
});

test('every version from 1 to 6 is read and others are refused', () => {
    const document = normalizeImport({ source, root: { kind: 'view', sourceId: 'root', children: [] } });
    assert.deepEqual([...DESIGN_IR_VERSIONS], [1, 2, 3, 4, 5, 6]);
    for (const version of [1, 2, 3, 4, 5, 6]) {
        assert.ok(validateDesignIR({ ...document, schemaVersion: version }).valid, `version ${version}`);
        assert.ok(matches({ ...document, schemaVersion: version }, schema), `schema version ${version}`);
    }
    for (const version of [0, 7, 1.5]) {
        assert.ok(refused({ ...document, schemaVersion: version }, /schemaVersion/), `version ${version}`);
        assert.equal(matches({ ...document, schemaVersion: version }, schema), false, `schema version ${version}`);
    }
});

test('compositing stays version 2 vocabulary in every later version', () => {
    const blended = normalizeImport({ source, root: { kind: 'view', sourceId: 'root', children: [
        { kind: 'view', sourceId: 'a', compositing: { blendMode: 'multiply' }, children: [] }] } });
    assert.equal(blended.schemaVersion, 2);
    assert.ok(refused({ ...blended, schemaVersion: 1 }, /schemaVersion/) ||
        !validateDesignIR({ ...blended, schemaVersion: 1 }).valid);
    assert.equal(matches({ ...blended, schemaVersion: 1 }, schema), false);
    for (const version of [2, 3, 4, 5, 6]) {
        assert.ok(validateDesignIR({ ...blended, schemaVersion: version }).valid, `version ${version}`);
        assert.ok(matches({ ...blended, schemaVersion: version }, schema), `schema version ${version}`);
    }
});

test('SVG opacity and masks lower into compositing, not vector paint keys', () => {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">
      <mask id="m"><rect width="10" height="20" fill="#ffffff"/></mask>
      <g id="g" opacity="0.5" mask="url(#m)"><rect width="20" height="20" fill="#ff0000"/></g></svg>`;
    const { document } = svgAssetToDesignIR(svg, { source });
    assert.equal(document.schemaVersion, 6);
    const nodes = [];
    const visit = (node) => { nodes.push(node); node.children.forEach(visit); };
    visit(document.root);
    const group = nodes.find((node) => node.id === 'assets/svg:1_g');
    assert.deepEqual(group.compositing, { opacity: 0.5 });
    const mask = nodes.find((node) => node.id === 'assets/svg:1_g:mask');
    assert.deepEqual(mask.compositing, { mask: 'luminance' });
    const content = nodes.find((node) => node.id === 'assets/svg:1_g:content');
    assert.deepEqual(content.compositing, { maskRef: 'assets/svg:1_g:mask' });
    for (const node of nodes) {
        for (const key of ['opacity', 'maskMode', 'isolation']) {
            assert.equal(node.properties.paint?.[key], undefined, `${node.id} paint.${key}`);
        }
    }
});

test('the required version counts material references, recursively', async () => {
    const { requiredDesignIRVersion } = await import('../src/index.js');
    const node = (extra = {}, children = []) => ({ kind: 'view', properties: {}, children, ...extra });
    const glass = { id: 'vellum.frosted-glass', values: {} };
    assert.equal(requiredDesignIRVersion(node({ material: glass })), 3, 'material only');
    assert.equal(requiredDesignIRVersion(node({}, [node({}, [node({ material: glass })])])), 3, 'nested material');
    assert.equal(requiredDesignIRVersion(node({ compositing: { opacity: 0.5 } }, [node({ material: glass })])), 3, 'material and compositing');
    assert.equal(requiredDesignIRVersion(node({ material: glass }, [node({ kind: 'path' })])), 6, 'material and a vector field');
    assert.equal(requiredDesignIRVersion(node({ compositing: { opacity: 0.5 } })), 2, 'compositing only');
    assert.equal(requiredDesignIRVersion(node()), 1, 'nothing');
});
