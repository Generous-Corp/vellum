// Checks the field port against values the native field code computed
// (fixtures/superconvolver-native/field-golden.json): the seed hash, the flow
// field, and every tracer's head, palette and head alpha at the native
// capture's state. It also checks the mark layout and that a frame is a pure
// function of its time.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

import {
    MARK_DOT, MARK_GLOW, MARK_HEADER, MARK_LINE, fieldFlowAt, fieldHash, fieldMarks,
} from '../views/superconvolver_field.js';

const here = dirname(fileURLToPath(import.meta.url));
const golden = JSON.parse(await readFile(
    join(here, 'fixtures', 'superconvolver-native', 'field-golden.json'), 'utf8'));
const f = Math.fround;

// The hash. The native platform's sinf is not correctly rounded, so some
// seeds land one float step away; the port must still agree on most.
// DESIGN: the 80% floor.
const hashMatches = golden.hash.filter(([k, value]) => fieldHash(k) === f(value)).length;
assert.ok(hashMatches >= 0.8 * golden.hash.length,
    `hash agrees with native on ${hashMatches} of ${golden.hash.length}`);
assert.equal(fieldHash(0), f(golden.hash[0][1]), 'hash(0)');

// The flow, to one part in a million. DESIGN: the tolerance.
for (const [x, y, t, vx, vy] of golden.flowAt) {
    const [ax, ay] = fieldFlowAt(f(x), f(y), t);
    assert.ok(Math.abs(ax - vx) < 1e-6 && Math.abs(ay - vy) < 1e-6,
        `flow at ${x}, ${y}, ${t}: ${ax}, ${ay} != ${vx}, ${vy}`);
}

// Seeded with the native hashes, every tracer's head lands within a
// thousandth of a pixel of native (DESIGN: the tolerance), with the native
// palette and head alpha.
const nativeHash = new Map();
golden.tracers.forEach(([h1, h2, h3, h4], k) => {
    nativeHash.set(3 * k + 1, f(h1));
    nativeHash.set(7 * k + 2, f(h2));
    nativeHash.set(13 * k + 5, f(h3));
    nativeHash.set(23 * k + 8, f(h4));
});
const W = 820;
const H = 560;
const state = {w: W, h: H, t: golden.t, flow: golden.flow, density: golden.density,
    energy: golden.energy, mode: 0};
const seeded = fieldMarks({...state, hash: k => nativeHash.get(k)});
assert.equal(seeded.count, 1 + golden.tracers.length * 32);
const PALETTE = [[234, 240, 248], [196, 214, 230], [228, 210, 182]];
golden.tracers.forEach(([, , , , headX, headY, palette, headAlpha], k) => {
    // Each tracer is 30 dots, then the head and its highlight.
    const head = MARK_HEADER * (1 + 32 * k + 30);
    const marks = seeded.marks;
    assert.equal(marks[head], MARK_DOT);
    const x = f(f(f(headX) * f(W * 0.54)) + W / 2);
    const y = f(f(f(headY) * f(H * 0.54)) + H / 2);
    assert.ok(Math.hypot(marks[head + 1] - x, marks[head + 2] - y) < 1e-3,
        `tracer ${k} head ${marks[head + 1]}, ${marks[head + 2]} != ${x}, ${y}`);
    assert.deepEqual([4, 5, 6].map(i => Math.round(marks[head + i] * 255)), PALETTE[palette],
        `tracer ${k} palette`);
    assert.equal(Math.round(marks[head + 7] * 255), headAlpha, `tracer ${k} head alpha`);
});

// The vignette leads: a glow of rgba(34, 42, 56) at 16, its middle at 5.
const own = fieldMarks(state);
assert.deepEqual(Array.from(own.marks.subarray(0, MARK_HEADER)),
    [MARK_GLOW, 410, 280, f(f(Math.max(f(W * 0.54), f(H * 0.54))) * f(1.25)),
        f(34 / 255), f(42 / 255), f(56 / 255), f(16 / 255), f(5 / 255)]);

// Currents: one line of 31 points per tracer, 1.1 wide.
const currents = fieldMarks({...state, mode: 1});
assert.equal(currents.count, 1 + golden.tracers.length);
assert.equal(currents.marks.length, MARK_HEADER * (1 + golden.tracers.length) + 62 * golden.tracers.length);
assert.deepEqual(Array.from(currents.marks.subarray(MARK_HEADER, MARK_HEADER + 3)),
    [MARK_LINE, 31, f(1.1)]);
// Field: one glow per fog emitter, 220 + 9 density.
const fog = fieldMarks({...state, mode: 2});
assert.equal(fog.count, 1 + 220 + 9 * golden.density);
assert.equal(fog.marks[MARK_HEADER], MARK_GLOW);

// A frame is a pure function of its arguments, and time moves it.
assert.deepEqual(fieldMarks(state).marks, own.marks);
assert.notDeepEqual(fieldMarks({...state, t: state.t + 1 / 60}).marks, own.marks);

console.log(`superconvolver field: ok (hash agrees on ${hashMatches} of ${golden.hash.length})`);
