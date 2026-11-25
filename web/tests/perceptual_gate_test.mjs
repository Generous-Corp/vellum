// Checks the shared perceptual and ink gate on images made here.
import assert from 'node:assert/strict';

import {
    INK_DISTANCE, INK_FLOOR, SSIM_FLOOR, lumaPlane, reduceLuma, regionInk, ssim, verdict,
} from './perceptual_gate.js';

function image(width, height, pixel) {
    const data = new Uint8ClampedArray(width * height * 4);
    for (let y = 0; y < height; ++y) {
        for (let x = 0; x < width; ++x) data.set([...pixel(x, y), 255], 4 * (y * width + x));
    }
    return {width, height, data};
}

// SSIM: 1 for an image against itself, symmetric, lower for a shifted
// structure, and lowest for structure against a flat field.
const stripes = image(48, 32, (x, y) => ((x >> 3) + (y >> 3)) % 2 ? [220, 220, 220] : [10, 12, 16]);
const shifted = image(48, 32, (x, y) => (((x + 1) >> 3) + (y >> 3)) % 2 ? [220, 220, 220] : [10, 12, 16]);
const flat = image(48, 32, () => [10, 12, 16]);
const a = lumaPlane(stripes);
assert.ok(Math.abs(ssim(a, a) - 1) < 1e-12);
const moved = ssim(a, lumaPlane(shifted));
assert.ok(Math.abs(moved - ssim(lumaPlane(shifted), a)) < 1e-12, 'symmetric');
const empty = ssim(a, lumaPlane(flat));
assert.ok(moved < 1 && empty < moved, `ordered: ${empty} < ${moved} < 1`);
assert.throws(() => ssim(a, lumaPlane(image(48, 30, () => [0, 0, 0]))), /SSIM of/);
assert.throws(() => ssim(lumaPlane(image(8, 8, () => [0, 0, 0])),
    lumaPlane(image(8, 8, () => [0, 0, 0]))), /window/);

// A 2 by 2 block reduces to its mean luma, rounded half up.
const block = image(4, 2, (x) => (x < 2 ? [255, 255, 255] : [x === 2 ? 1 : 2, 0, 0]));
assert.deepEqual(Array.from(reduceLuma(block).data), [255, 0]);
assert.deepEqual([reduceLuma(block).width, reduceLuma(block).height], [2, 1]);
const half = image(2, 2, (x, y) => (x + y === 0 ? [255, 255, 255] : [0, 0, 0]));
assert.deepEqual(Array.from(reduceLuma(half).data), [64], '63.75 rounds to 64');

// Ink: a pixel counts when it is more than INK_DISTANCE from its surface,
// and a region passes at INK_FLOOR of its area.
assert.equal(INK_DISTANCE, 8);
assert.equal(INK_FLOOR, 0.01);
assert.equal(SSIM_FLOOR, 0.9);
const surface = image(10, 10, () => [20, 20, 20]);
const at = (dx, n = 1) => image(10, 10, (x, y) => (y * 10 + x < n ? [20 + dx, 20, 20] : [20, 20, 20]));
assert.deepEqual(regionInk(at(8), surface), {pixels: 0, area: 100, fraction: 0, pass: false},
    'a pixel at the distance is surface');
assert.deepEqual(regionInk(at(9), surface), {pixels: 1, area: 100, fraction: 0.01, pass: true},
    'one pixel past it in a 100 pixel region meets the floor');
assert.equal(regionInk(image(20, 10, (x, y) => (x + y === 0 ? [255, 0, 0] : [20, 20, 20])),
    image(20, 10, () => [20, 20, 20])).pass, false, 'one pixel in 200 is a placeholder');
assert.throws(() => regionInk(at(9), image(5, 5, () => [0, 0, 0])), /differ in size/);

// The verdict names every failing region, and the SSIM below its floor.
const pass = {pixels: 5, area: 100, fraction: 0.05, pass: true};
const fail = {pixels: 0, area: 100, fraction: 0, pass: false};
assert.deepEqual(verdict({ink: {chip: pass, label: fail}, similarity: 0.95}).failures, ['ink:label']);
assert.deepEqual(verdict({ink: {chip: pass}, similarity: 0.89}).failures, ['ssim']);
assert.deepEqual(verdict({ink: {chip: pass}, similarity: Number.NaN}).failures, ['ssim']);
assert.equal(verdict({ink: {chip: pass}}).pass, true, 'no reference, no SSIM check');

console.log('perceptual gate: ok');
