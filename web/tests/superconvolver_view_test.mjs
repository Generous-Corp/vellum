// Checks the Superconvolver view's layout against values worked by hand from
// the native editor's formulas, its parameter mapping, and the slider value
// mapping, without a browser.
import assert from 'node:assert/strict';

import {trackValue} from '../session/gesture_tracker.js';
import {
    BYPASS, PARAMETERS, SLIDERS, createSuperconvolverView, superconvolverLayout,
} from '../views/superconvolver.js';

function near(actual, expected, name) {
    for (const key of ['x', 'y', 'width', 'height']) {
        assert.ok(Math.abs(actual[key] - expected[key]) < 1e-6,
            `${name}.${key}: ${actual[key]} != ${expected[key]}`);
    }
}

const sliders = ['mix', 'size', 'gain'];

// 820 by 560, the native screenshot size: s = 1, wide.
{
    const {bounds, scale, narrow} = superconvolverLayout(820, 560, sliders);
    assert.equal(scale, 1);
    assert.equal(narrow, false);
    // avail = 560 - 64 - 20 = 476; IR 199.92, spectrum 123.76, controls 128.32.
    near(bounds.ir, {x: 20, y: 64, width: 780, height: 199.92}, 'ir');
    near(bounds.spectrum, {x: 20, y: 275.92, width: 780, height: 123.76}, 'spectrum');
    near(bounds.controls, {x: 20, y: 411.68, width: 780, height: 128.32}, 'controls');
    near(bounds.loadIr, {x: 24, y: 44, width: 196, height: 38}, 'loadIr');
    near(bounds.mix, {x: 20, y: 411.68, width: 260, height: 128.32}, 'mix');
    near(bounds.size, {x: 280, y: 411.68, width: 260, height: 128.32}, 'size');
    near(bounds.gain, {x: 540, y: 411.68, width: 260, height: 128.32}, 'gain');
    near(bounds['mix/track'], {x: 34, y: 480.9728, width: 232, height: 4}, 'mix/track');
    near(bounds['gain/track'], {x: 554, y: 480.9728, width: 232, height: 4}, 'gain/track');
    near(bounds['size/label'], {x: 294, y: 454.9728, width: 232, height: 26}, 'size/label');
    near(bounds.bypass, {x: 700, y: 18, width: 96, height: 32}, 'bypass');
}

// 390 by 700: s = 1.25, narrow because 390 < 850.
{
    const {bounds, scale, narrow} = superconvolverLayout(390, 700, sliders);
    assert.equal(scale, 1.25);
    assert.equal(narrow, true);
    // top = 165, avail = 700 - 165 - 25 = 510; IR 214.2, spectrum 132.6, controls 133.2.
    near(bounds.ir, {x: 25, y: 165, width: 340, height: 214.2}, 'ir');
    near(bounds.spectrum, {x: 25, y: 394.2, width: 340, height: 132.6}, 'spectrum');
    near(bounds.controls, {x: 25, y: 541.8, width: 340, height: 133.2}, 'controls');
    near(bounds.loadIr, {x: 25, y: 115, width: 340, height: 45}, 'loadIr');
    near(bounds['size/track'], {x: 25 + 340 / 3 + 17.5, y: 613.728, width: 340 / 3 - 35, height: 5},
        'size/track');
    near(bounds['mix/label'], {x: 42.5, y: 558.728, width: 340 / 3 - 35, height: 55}, 'mix/label');
    near(bounds.bypass, {x: 240, y: 22.5, width: 120, height: 40}, 'bypass');
}

// A short canvas clamps s at 0.5.
assert.equal(superconvolverLayout(400, 200, sliders).scale, 0.5);

// The strip divides by the live count: two sliders, two columns.
{
    const {bounds} = superconvolverLayout(820, 560, ['mix', 'gain']);
    near(bounds.mix, {x: 20, y: 411.68, width: 390, height: 128.32}, 'two-column mix');
    near(bounds.gain, {x: 410, y: 411.68, width: 390, height: 128.32}, 'two-column gain');
    assert.equal(bounds.size, undefined);
}

// Parameter mapping is the plug-in's ids, and only the web set exists.
assert.deepEqual(PARAMETERS, {mix: 1, size: 2, gain: 3, bypass: 4});
assert.deepEqual(SLIDERS.map(slider => [slider.id, slider.param, slider.label]),
    [['mix', 1, 'Mix'], ['size', 2, 'Size'], ['gain', 3, 'Gain']]);
assert.deepEqual([BYPASS.id, BYPASS.param, BYPASS.label], ['bypass', 4, 'Bypass']);

const web = [1, 2, 3, 4].map(id => ({id}));
const view = createSuperconvolverView(web);
const bound = view.nodes.filter(node => 'param' in node);
assert.deepEqual(bound.map(node => [node.id, node.kind, node.param]),
    [['mix', 'slider', 1], ['size', 'slider', 2], ['gain', 'slider', 3], ['bypass', 'toggle', 4]]);
assert.ok(view.nodes.every(node => !/engine|rooms|flow/i.test(node.id)));

// An adapter without Size and Bypass gets neither control.
const partial = createSuperconvolverView([{id: 1}, {id: 3}]);
assert.deepEqual(partial.nodes.filter(node => 'param' in node).map(node => node.param), [1, 3]);
const partialBounds = partial.layout(820, 560);
assert.equal(partialBounds.bypass, undefined);
near(partialBounds.gain, {x: 410, y: 411.68, width: 390, height: 128.32}, 'partial gain');

// The layout's text styles follow the mode.
view.layout(820, 560);
assert.deepEqual(view.textStyle('mix/value'), {fontSize: 14, align: 'right'});
view.layout(390, 700);
assert.deepEqual(view.textStyle('mix/value'), {fontSize: 13 * 1.25, align: 'center'});

// The slider value is absolute along the track, clamped at both ends.
const gain = {minValue: -24, maxValue: 24};
const track = {x: 100, y: 0, width: 200, height: 4};
assert.equal(trackValue(gain, track, 100), -24);
assert.equal(trackValue(gain, track, 200), 0);
assert.equal(trackValue(gain, track, 350), 24);
assert.equal(trackValue(gain, track, 0), -24);

console.log('superconvolver view: ok');
