// Feeds a compiled vector asset through the shell's builder with a recording
// stand-in for the Wasm module, and checks each call carries the asset's
// fields to the C ABI unchanged.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { addVectorAsset } from '../shell/vector-canvas.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const rendered = JSON.parse(await readFile(
    join(root, 'fixtures', 'vector-assets', 'compiled-only.rendered.json'), 'utf8'));
const node = rendered.tree.children[0];
const calls = [];
const module = {
    cwrap(symbol, returns, args) {
        assert.equal(returns, 'number');
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
assert.deepEqual(calls[0], ['vellum_web_begin_vector', node.id, 1, 2, 200, 120, 0, 0, 200, 120,
    node.asset.contentHash]);
assert.equal(calls.at(-1)[0], 'vellum_web_end_vector');
const paths = calls.filter((call) => call[0] === 'vellum_web_vector_path');
assert.equal(paths.length, 3);
const gradients = calls.filter((call) => call[0] === 'vellum_web_vector_gradient');
assert.equal(gradients.length, 3);
const wide = node.asset.ops.find((op) => op.op === 'path' && typeof op.fill === 'object');
assert.deepEqual(gradients[0].slice(-6), wide.fill.transform);
assert.equal(gradients[2][2], 2, 'the focal radial is a two-circle radial');
const stops = calls.filter((call) => call[0] === 'vellum_web_vector_stop');
assert.equal(stops.length, 6);
assert.deepEqual(stops[0], ['vellum_web_vector_stop', 0, 0, 1, 0, 0, 1]);
console.log('vector canvas builder test passed');
