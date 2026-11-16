import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { createApp, materializeDesign, mount } from '../src/index.js';
import { svgAssetToDesignIR } from '../../vellum-design-ir/src/index.js';

const fixtures = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'fixtures',
    'vector-assets');
const update = process.env.VELLUM_UPDATE_VECTOR_FIXTURES === '1';

const source = { key: 'assets', adapter: 'svg', adapterVersion: '1', formatVersion: '1.1',
    revision: 'fixture' };
const accent = (value) => ({ 'assets.brand.accent': { $type: 'color', $value: value } });

// The rendered trees the native GPU tests materialize, each generated from
// an SVG fixture through DesignIR and the authoring runtime.
const cases = [
    { name: 'gradients', svg: 'gradients', mode: 'subtree', tokens: accent('#e11d48') },
    { name: 'gradients-retheme', svg: 'gradients', mode: 'subtree', tokens: accent('#22c55e') },
    { name: 'gradients-compiled', svg: 'gradients', mode: 'compiled', tokens: accent('#e11d48'),
        options: { nodeBudget: 4 } },
    { name: 'clip-mask', svg: 'clip-mask', mode: 'subtree' },
    { name: 'use', svg: 'use', mode: 'subtree' },
    { name: 'compiled-only', svg: 'compiled-only', mode: 'compiled' },
    { name: 'over-budget', svg: 'over-budget', mode: 'compiled' },
];

async function render(entry) {
    const svg = await readFile(join(fixtures, `${entry.svg}.svg`), 'utf8');
    const { mode, document } = svgAssetToDesignIR(svg, {
        source, tokens: entry.tokens,
        colorTokens: entry.tokens ? { '#e11d48': 'assets.brand.accent' } : undefined,
        ...entry.options,
    });
    const width = document.root.properties.layout.width;
    const height = document.root.properties.layout.height;
    const bridge = mount(createApp({
        render: () => materializeDesign(document, { viewport: { width, height } }),
    }));
    return { mode, document, rendered: `${bridge.renderJSON()}\n` };
}

for (const entry of cases) {
    test(`the ${entry.name} rendered tree is generated from its SVG fixture`, async () => {
        const { mode, rendered } = await render(entry);
        assert.equal(mode, entry.mode);
        const path = join(fixtures, `${entry.name}.rendered.json`);
        if (update) await writeFile(path, rendered);
        assert.equal(await readFile(path, 'utf8'), rendered,
            'run with VELLUM_UPDATE_VECTOR_FIXTURES=1 to regenerate');
    });
}

function nodes(tree, output = new Map()) {
    output.set(tree.id, tree);
    for (const child of tree.children ?? []) nodes(child, output);
    return output;
}

test('a retheme changes only the token-bound node of an imported subtree', async () => {
    const before = nodes(JSON.parse((await render(cases[0])).rendered).tree);
    const after = nodes(JSON.parse((await render(cases[1])).rendered).tree);
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

test('the compiled form of one asset is a single vector node', async () => {
    const tree = JSON.parse((await render(cases[2])).rendered).tree;
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

test('vector asset fields are not read below DesignIR version 6', async () => {
    const { document } = await render(cases[0]);
    assert.equal(document.schemaVersion, 6);
    for (const version of [1, 2, 5]) {
        assert.throws(() => materializeDesign({ ...document, schemaVersion: version },
            { viewport: { width: 200, height: 120 } }), /needs schemaVersion 6/);
    }
});

test('a material-bearing node is refused at version 3 and 6, never dropped', async () => {
    const { document } = await render(cases[0]);
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
