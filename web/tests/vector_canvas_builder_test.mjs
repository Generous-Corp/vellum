// Feeds a compiled vector asset through the shell's builder with a recording
// stand-in for the Wasm module, and checks each call carries the asset's
// fields to the C ABI unchanged.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { addVectorAsset, addVectorNode } from '../shell/vector-canvas.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const rendered = JSON.parse(await readFile(
    join(root, 'fixtures', 'vector-assets', 'compiled-only.rendered.json'), 'utf8'));
const node = rendered.tree.children[0];
const calls = [];
const arities = new Map();
const module = {
    cwrap(symbol, returns, args) {
        assert.equal(returns, 'number');
        arities.set(symbol, args.length);
        return (...values) => {
            assert.equal(values.length, args.length, `${symbol} takes ${args.length} arguments`);
            values.forEach((value, index) => assert.equal(typeof value, args[index],
                `${symbol} argument ${index}`));
            calls.push([symbol, ...values]);
            return 1;
        };
    },
};
addVectorAsset(module, node.id, 1, 2, 200, 120, node.asset);
assert.deepEqual(calls[0], ['vellum_web_begin_vector', node.id, 1, 2, 200, 120, 1, 0, 0, 200, 120,
    node.asset.contentHash]);
assert.equal(calls.at(-1)[0], 'vellum_web_end_vector');
const paths = calls.filter((call) => call[0] === 'vellum_web_vector_path');
assert.equal(paths.length, 3);
const gradients = calls.filter((call) => call[0] === 'vellum_web_vector_gradient');
assert.equal(gradients.length, 3);
const wide = node.asset.ops.find((op) => op.op === 'path' && typeof op.fill === 'object');
assert.deepEqual(gradients[0].slice(-6), wide.fill.transform);
assert.equal(gradients[2][2], 2, 'the focal radial is a two-circle radial');
assert.deepEqual(gradients[0].slice(3, 6), [0, 0, 0], 'pad, srgb interpolation, srgb stops');

const stops = calls.filter((call) => call[0] === 'vellum_web_vector_stop');
assert.equal(stops.length, 6);
assert.deepEqual(stops[0], ['vellum_web_vector_stop', 0, 0, 1, 0, 0, 1]);
const conic = { ...node.asset, ops: [{ op: 'path', d: 'M0 0H1V1Z', stroke: 'none',
    fill: { gradient: { kind: 'conic', interpolation: 'oklab', colorSpace: 'display-p3',
        stops: [{ position: 0, color: '#000000' }, { position: 1, color: '#ffffff' }] } } }] };
calls.length = 0;
addVectorAsset(module, 'conic', 0, 0, 1, 1, conic);
const encoded = calls.find((call) => call[0] === 'vellum_web_vector_gradient');
assert.deepEqual(encoded.slice(2, 6), [3, 0, 2, 2], 'conic, pad, oklab, display-p3');
assert.throws(() => addVectorAsset(module, 'bad', 0, 0, 1, 1, { ...conic, ops: [{ ...conic.ops[0],
    fill: { gradient: { ...conic.ops[0].fill.gradient, kind: 'spiral' } } }] }), /cannot carry/);

// The demo adds a rendered Vector node with its opacity, and a group's clip
// presence travels apart from its path data, so an empty clip stays a clip.
const square = { op: 'path', d: 'M0 0H10V10H0Z', fill: '#ff0000', stroke: 'none' };
const clipped = { contentHash: node.asset.contentHash, viewBox: [0, 0, 10, 10],
    ops: [{ op: 'group', clip: { d: '' } }, square, { op: 'end' }, { op: 'group' }, square, { op: 'end' }] };
calls.length = 0;
addVectorNode(module, { type: 'vector', id: 'faded', asset: clipped,
    style: { width: 10, height: 10, opacity: 0.5 } }, 0, 0, 10, 10);
assert.deepEqual(calls[0].slice(0, 7), ['vellum_web_begin_vector', 'faded', 0, 0, 10, 10, 0.5]);
const groups = calls.filter((call) => call[0] === 'vellum_web_vector_group');
assert.deepEqual(groups.map((call) => call.slice(8)), [[1, '', 0], [0, '', 0]],
    'an empty clip is present, an absent clip is not');
calls.length = 0;
addVectorNode(module, { type: 'vector', id: 'plain', asset: clipped, style: { width: 10, height: 10 } },
    0, 0, 10, 10);
assert.equal(calls[0][6], 1, 'a node without opacity draws opaque');

// Each wrapped symbol takes as many arguments as its C definition declares.
const runtime = await readFile(join(root, 'web', 'src', 'web_runtime.cpp'), 'utf8');
for (const [symbol, count] of arities) {
    const definition = new RegExp(`EMSCRIPTEN_KEEPALIVE int ${symbol}\\(([^)]*)\\)`).exec(runtime);
    assert.ok(definition, `${symbol} is defined`);
    const parameters = definition[1].trim() ? definition[1].split(',').length : 0;
    assert.equal(parameters, count, `${symbol} takes ${parameters} arguments in C`);
}
console.log('vector canvas builder test passed');
