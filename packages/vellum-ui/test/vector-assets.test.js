import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { createApp, materializeDesign, mount } from '../src/index.js';
import { normalizeImport } from '../../vellum-design-ir/src/index.js';

// The rendered trees the native GPU tests materialize. They are static here;
// the SVG importer that produced them is in its own follow-up change.
const fixtures = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'fixtures',
    'vector-assets');
const fixtureTree = async (name) =>
    JSON.parse(await readFile(join(fixtures, `${name}.rendered.json`), 'utf8')).tree;

// A version 6 document with one path node and one compiled vector node.
const vectorDocument = () => normalizeImport({
    source: { key: 'assets', adapter: 'svg', adapterVersion: '1', formatVersion: '1.1', revision: 'fixture' },
    root: { kind: 'view', sourceId: 'svg', properties: { layout: { width: 200, height: 120 }, paint: {} }, children: [
        { kind: 'path', sourceId: 'svg.0', properties: { layout: { x: 0, y: 0, width: 0, height: 0 },
            path: { d: 'M0 0L10 0L10 10Z', fillRule: 'nonzero' },
            paint: { fill: '#ff0000', stroke: 'none' } }, children: [] },
        { kind: 'vector', sourceId: 'svg.1', properties: { layout: { x: 0, y: 0, width: 20, height: 20 },
            vector: { asset: { contentHash: `sha256:${'a'.repeat(64)}`, viewBox: [0, 0, 20, 20],
                ops: [{ op: 'path', d: 'M0 0H20V20Z', fill: '#0000ff' }] } } }, children: [] },
    ] },
});

test('a vector document materializes into path and vector nodes', () => {
    const document = vectorDocument();
    assert.equal(document.schemaVersion, 6);
    const bridge = mount(createApp({
        render: () => materializeDesign(document, { viewport: { width: 200, height: 120 } }),
    }));
    const tree = JSON.parse(bridge.renderJSON()).tree;
    assert.deepEqual(tree.children.map((child) => child.type), ['path', 'vector']);
    assert.equal(tree.children[1].asset.ops[0].fill, '#0000ff');
});

function nodes(tree, output = new Map()) {
    output.set(tree.id, tree);
    for (const child of tree.children ?? []) nodes(child, output);
    return output;
}

test('the retheme fixtures differ only in the token-bound node', async () => {
    const before = nodes(await fixtureTree('gradients'));
    const after = nodes(await fixtureTree('gradients-retheme'));
    assert.deepEqual([...before.keys()], [...after.keys()], 'stable identities survive the retheme');
    const changed = [...before.keys()].filter((id) => {
        const { children: left, ...own } = before.get(id);
        const { children: right, ...next } = after.get(id);
        void left;
        void right;
        return JSON.stringify(own) !== JSON.stringify(next);
    });
    assert.deepEqual(changed, ['assets/svg:6_swatch']);
    assert.equal(before.get('assets/svg:6_swatch').fill, '#e11d48');
    assert.equal(after.get('assets/svg:6_swatch').fill, '#22c55e');
});

test('the compiled fixture is a single vector node', async () => {
    const tree = await fixtureTree('gradients-compiled');
    assert.equal(tree.children.length, 1);
    assert.equal(tree.children[0].type, 'vector');
    assert.equal(tree.children[0].id, 'assets/svg:compiled');
    assert.match(tree.children[0].asset.contentHash, /^sha256:[0-9a-f]{64}$/);
});

test('the vector primitive refuses malformed compiled assets', () => {
    const asset = { contentHash: `sha256:${'a'.repeat(64)}`, viewBox: [0, 0, 10, 10], ops: [] };
    const tree = (value) => () => mount(createApp({
        render: () => ({ $$typeof: Symbol.for('vellum.element'), type: 'vector',
            props: { id: 'v', asset: value, style: { width: 10, height: 10 } } }),
    })).renderJSON();
    assert.doesNotThrow(tree(asset));
    assert.throws(tree({ ...asset, contentHash: 'md5:x' }), /contentHash/);
    assert.throws(tree({ ...asset, ops: [{ op: 'group' }] }), /leaves a group open/);
    assert.throws(tree({ ...asset, ops: [{ op: 'end' }] }), /not open/);
    assert.throws(tree({ ...asset, ops: [{ op: 'path', d: 'M0 0', fill: 'red' }] }), /hex color/);
    assert.throws(tree({ ...asset, ops: [{ op: 'path', d: 'M0 0', script: 1 }] }), /unsupported/);
    assert.throws(tree({ ...asset, viewBox: [0, 0, 0, 10] }), /positive/);
    const miter = (value) => tree({ ...asset, ops: [{ op: 'path', d: 'M0 0H10V10Z', strokeMiterLimit: value }] });
    assert.throws(miter(0), /strokeMiterLimit must be at least 1/);
    assert.throws(miter(0.5), /strokeMiterLimit must be at least 1/);
    assert.doesNotThrow(miter(1));
    assert.throws(tree({ ...asset, viewBox: [0, 0, 1e100, 10] }), /float32/);
    assert.throws(tree({ ...asset, viewBox: [0, 0, 1e-50, 10] }), /positive/);
    assert.throws(tree({ ...asset, ops: [{ op: 'group', transform: [1e39, 0, 0, 1, 0, 0] }, { op: 'end' }] }), /float32/);
    const ramp = { angle: 90, stops: [{ position: 0, color: '#000000' }, { position: 1, color: '#ffffff' }] };
    const painted = (fill) => tree({ ...asset, ops: [{ op: 'path', d: 'M0 0H10V10Z', fill }] });
    assert.doesNotThrow(painted({ gradient: ramp, transform: [1, 0, 0, 1, 0, 0] }));
    assert.throws(painted({ gradient: { ...ramp, repeatLength: 1 } }), /repeat length/);
    assert.throws(painted({ gradient: ramp, transform: [0, 0, 0, 0, 0, 0] }), /invertible/);
    assert.throws(painted({ gradient: ramp, transform: [1e-30, 0, 0, 1e-30, 0, 0] }), /invertible/);
    const dashed = (strokeDasharray) => tree({ ...asset, ops: [{ op: 'path', d: 'M0 0H10', stroke: '#ff0000',
        strokeDasharray }] });
    assert.throws(dashed([0, 0]), /must not sum to zero/);
    assert.doesNotThrow(dashed([0, 1]));
    assert.doesNotThrow(dashed([]));
});

test('vector asset fields are not read below DesignIR version 6', () => {
    const document = vectorDocument();
    assert.equal(document.schemaVersion, 6);
    for (const version of [1, 2, 5]) {
        assert.throws(() => materializeDesign({ ...document, schemaVersion: version },
            { viewport: { width: 200, height: 120 } }), /needs schemaVersion 6/);
    }
});

test('a material-bearing node is refused at version 3 and 6, never dropped', () => {
    const document = vectorDocument();
    for (const version of [3, 6]) {
        const material = structuredClone(document);
        material.schemaVersion = version;
        const target = version === 3 ? material.root : material.root.children[0];
        if (version === 3) {
            material.root.children = [];
        }
        target.material = { id: 'vellum.frosted-glass', values: { blur: 12 } };
        assert.throws(() => materializeDesign(material, { viewport: { width: 200, height: 120 } }),
            /unsupported material/, `version ${version}`);
    }
});

test('hidden descendants are held to the vector version gate', () => {
    const document = vectorDocument();
    const hidden = (node) => {
        node.properties.layout.display = 'none';
        return node;
    };
    for (const index of [0, 1]) {
        const copy = structuredClone(document);
        copy.root.children = [hidden(copy.root.children[index])];
        assert.doesNotThrow(() => materializeDesign(copy, { viewport: { width: 200, height: 120 } }));
        for (const version of [1, 5]) {
            if (index === 0) {
                assert.doesNotThrow(() => materializeDesign({ ...copy, schemaVersion: version },
                    { viewport: { width: 200, height: 120 } }), `path version ${version}`);
            } else {
                assert.throws(() => materializeDesign({ ...copy, schemaVersion: version },
                    { viewport: { width: 200, height: 120 } }), /needs schemaVersion 6/, `vector version ${version}`);
            }
        }
    }
});
