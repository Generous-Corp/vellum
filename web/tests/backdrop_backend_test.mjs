// Draws backdrop marks through the session's Canvas2D backend into a
// recording context and checks each mark reaches Canvas2D as the core's
// `backdrop_marks.hpp` layout describes it, additively. Also checks the
// session's binding of `vellum_web_set_backdrop` against its C definition and
// the core's exports.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

import {drawBackdrop} from '../session/canvas2d_backend.js';

const calls = [];
const gradients = [];
const context = {
    globalCompositeOperation: 'source-over',
    lineWidth: 1,
    set fillStyle(value) { calls.push(['fillStyle', value]); },
    set strokeStyle(value) { calls.push(['strokeStyle', value]); },
    save() { calls.push(['save']); },
    restore() { calls.push(['restore']); this.globalCompositeOperation = 'source-over'; },
    beginPath() { calls.push(['beginPath']); },
    arc(...args) { calls.push(['arc', ...args, this.globalCompositeOperation]); },
    fill() { calls.push(['fill', this.globalCompositeOperation]); },
    moveTo(...args) { calls.push(['moveTo', ...args]); },
    lineTo(...args) { calls.push(['lineTo', ...args]); },
    stroke() { calls.push(['stroke', this.lineWidth, this.globalCompositeOperation]); },
    createRadialGradient(...args) {
        const gradient = {args, stops: []};
        gradient.addColorStop = (position, color) => gradient.stops.push([position, color]);
        gradients.push(gradient);
        return gradient;
    },
};

const marks = new Float32Array([
    0, 10, 20, 1.5, 1, 0.5, 0, 0.25, 0,
    1, 30, 40, 26, 0, 1, 0, 0.5, 0.125,
    2, 3, 1.25, 0, 0, 0, 1, 1, 0, 1, 2, 3, 4, 5, 6,
    // An unknown kind ends the backdrop rather than misreading what follows.
    7, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 1, 1, 1, 1, 1, 1, 1, 0,
]);
drawBackdrop(context, marks);
const lighter = calls.filter(call => ['arc', 'fill', 'stroke'].includes(call[0]));
assert.ok(lighter.every(call => call.at(-1) === 'lighter'), 'every mark is drawn additively');
assert.equal(context.globalCompositeOperation, 'source-over', 'the blend is restored after');
assert.deepEqual(calls.filter(call => call[0] === 'arc').map(call => call.slice(1, 4)),
    [[10, 20, 1.5], [30, 40, 26]]);
assert.deepEqual(calls.find(call => call[0] === 'fillStyle'), ['fillStyle', 'rgba(255,128,0,0.25)']);
assert.deepEqual(gradients.map(gradient => gradient.args), [[30, 40, 0, 30, 40, 26]]);
assert.deepEqual(gradients[0].stops, [[0, 'rgba(0,255,0,0.5)'], [0.38, 'rgba(0,255,0,0.125)'],
    [1, 'rgba(0,255,0,0)']]);
assert.deepEqual(calls.filter(call => call[0] === 'moveTo' || call[0] === 'lineTo'),
    [['moveTo', 1, 2], ['lineTo', 3, 4], ['lineTo', 5, 6]]);
assert.deepEqual(calls.filter(call => call[0] === 'stroke'), [['stroke', 1.25, 'lighter']]);
assert.deepEqual(calls.find(call => call[0] === 'strokeStyle'), ['strokeStyle', 'rgba(0,0,255,1)']);
assert.equal(calls.filter(call => call[0] === 'fill').length, 2, 'nothing after the unknown kind');

// The session binds the entry point with the C definition's arity, and the
// core exports it with the allocator and heap view the session uses.
const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const session = await readFile(join(root, 'web', 'session', 'editor_session.js'), 'utf8');
const runtime = await readFile(join(root, 'web', 'src', 'web_runtime.cpp'), 'utf8');
const build = await readFile(join(root, 'web', 'CMakeLists.txt'), 'utf8');
const binding = /cwrap\('vellum_web_set_backdrop', 'number', \[([^\]]*)\]\)/.exec(session);
assert.ok(binding, 'the session binds vellum_web_set_backdrop');
const definition = /EMSCRIPTEN_KEEPALIVE int vellum_web_set_backdrop\(([^)]*)\)/.exec(runtime);
assert.ok(definition, 'the core defines vellum_web_set_backdrop');
assert.equal(binding[1].split(',').length, definition[1].split(',').length);
for (const symbol of ['_vellum_web_set_backdrop', '_malloc', '_free']) {
    assert.ok(build.includes(`'${symbol}'`), `the core exports ${symbol}`);
}
assert.ok(/EXPORTED_RUNTIME_METHODS=\[[^\]]*'HEAPF32'/.test(build), 'the core exports HEAPF32');

console.log('backdrop backend: ok');
